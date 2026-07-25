# Rediseño de `content_generation_task`: la semana gratis en jobs encadenados

## Objetivo

`content_generation_task` (genera los 7 días de prueba gratis al registrarse un usuario
nuevo) sigue siendo un job monolítico de RQ: un solo proceso que genera texto + imagen +
reel de los 7 posts secuencialmente, ocupando 1 worker completo (de 3 disponibles,
`docker-compose.yml`, cola `default` compartida) durante ~10-15 min. El mismo día se
rediseñó `generate_next_month` (el job del mes completo, tras el pago) exactamente por
este problema — un job monolítico se cayó por timeout en su primera prueba real. Este
spec aplica el mismo patrón ya construido y probado a `content_generation_task`, para
que un 4º/5º registro simultáneo no tenga que esperar en cola detrás de un job de 10-15
min ocupando un worker entero.

**El objetivo NO es que el usuario individual vea su semana lista más rápido** — es
liberar workers antes para soportar más registros simultáneos. La latencia total
percibida por un usuario que se queda solo, sin nadie más registrándose al mismo tiempo,
no cambia.

## Contexto

- El producto ya asume flujo asíncrono: la pantalla de análisis (`results.html`) le dice
  explícitamente al usuario "No hace falta que esperes aquí... te avisamos por correo.
  Puedes cerrar esta pestaña con confianza." Nadie se queda mirando la barra de
  progreso como requisito del flujo.
- `calendar_review_view` ya no depende de `job.status` para mostrar el calendario —
  muestra los `ContentPost` que existan en BD, con placeholder `📸` para los que aún no
  tienen imagen (mismo patrón ya construido hoy para el mes completo). Esto habilita
  separar texto de imagen sin romper esa vista.
- El disparo del trial (`Subscription.status='trialing'`, filtrado por `plan__name='User'`,
  fix del mismo día) ya ocurre apenas se crea el `ContentCalendar`, antes de que existan
  los 7 posts — **sin cambios, no se toca**.
- `backfill_image_task(post_id)` ya existe, ya es idempotente (`if post.image_url:
  return`), y ya se reusó hoy como unidad atómica de trabajo para `generate_next_month`
  — mismo candidato aquí, sin cambios a su cuerpo.
- El auditor de consistencia de marca (`brand_consistency_qc.py`) está enganchado en
  `TextGenerator.generate()` (audita las 7 captions) y en `ReelScriptGenerator` (audita
  hook/CTA/narración/`scene_prompts` del reel) — ninguno de los dos hooks se mueve ni se
  toca con este rediseño, solo cambia el momento/worker en el que ya se ejecutan. La
  falta de hook en `image_generator.py` es un hallazgo aparte, ya registrado en memoria
  (`project_quality_roadmap_ideas.md`, punto 9) — **fuera de alcance de este spec**.

## Decisiones confirmadas

1. **Alcance: solo la generación de contenido** (texto + imagen + reel de los 7 posts).
   El análisis de marca previo (`STAGE_WEB`/`STAGE_LOGO`/`STAGE_POSTS`) no se toca — si
   también es lento, es un hallazgo/brainstorm aparte.
2. **De cara al usuario: sin cambios de UX ni de copy.** El job se marca `DONE` y el
   correo de bienvenida (`send_initial`) se manda **solo cuando las 7 imágenes/reel ya
   están listas** — igual que hoy. Se descartó la alternativa de marcar `DONE` en cuanto
   el texto está listo (más rápido en teoría) porque el objetivo real es liberar
   workers, no acelerar la percepción del usuario individual, y cambiar ese momento
   habría requerido ajustar el copy del correo sin necesidad real.
3. **Barra de progreso: se simplifica.** Hoy avanza con un tick fino por cada imagen
   (80%→87%→88%...→100%). Con las 7 imágenes generándose en paralelo en jobs/workers
   distintos, mantener ese detalle exigiría un incremento seguro ante condiciones de
   carrera (varios workers escribiendo `job.progress` casi al mismo tiempo). Se
   descarta: el progreso salta de ~87% (texto listo) a 100% (cierre) sin pasos
   intermedios — en la práctica nadie se queda viendo la barra (ver Contexto).
4. **Arquitectura: extraer un helper compartido**, no duplicar la mecánica de RQ ni
   prescindir de `Dependency`. Se evaluaron 3 opciones:
   - Duplicar `_enqueue_week_images`/`_week_closing_task` en versiones paralelas
     específicas del trial — cero riesgo sobre el código del mes, pero mantiene dos
     copias del mismo patrón de reintentos/timeouts.
   - **(Elegida)** Extraer la parte genérica ("encola N `backfill_image_task` + un job
     de cierre que depende de todos") a un helper único, reusado por el flujo del mes
     (ya existente) y por el flujo del trial (nuevo).
   - Prescindir de `Dependency` y que cada `backfill_image_task` revise si ya no quedan
     imágenes pendientes para disparar el cierre él mismo — descartada: reintroduce una
     condición de carrera (2 imágenes terminando casi simultáneamente podrían disparar
     el cierre dos veces) que `Dependency` nativo de RQ ya resuelve.
5. **Reintentos y timeouts por imagen: mismos valores que el flujo del mes** —
   `Retry(max=3, interval=[10, 20, 40])` por `backfill_image_task`, `job_timeout=600`
   para el post `reel` de la semana, `300` para `single`/`carousel`. El job de cierre:
   `Retry(max=2, interval=[10, 30])`, `job_timeout=120`.
6. **Fallo parcial: el cierre avanza igual.** `Dependency(jobs=[...los 7...],
   allow_failure=True)` — si 1 o más posts fallan de forma permanente tras sus
   reintentos, el trial se marca `DONE` igual (con esos posts en `📸`), consistente con
   la misma política ya aceptada para el mes completo.
7. **Métrica de duración (`CONTENT_GENERATION_DURATION`)**: hoy se mide con un
   `time.monotonic()` local al job monolítico, que no sobrevive entre jobs de RQ
   distintos. Se pasa el timestamp de inicio (`time.time()`, no `time.monotonic()` —
   necesita ser comparable entre procesos) como argumento a través de la cadena
   (`content_generation_task` → `_enqueue_trial_images` → `_trial_closing_task`), que
   calcula la duración total al cerrar. El significado de la métrica no cambia: sigue
   siendo el tiempo total desde que arranca la generación hasta que todo (texto +
   imágenes) está listo.

## Diseño técnico

### Helper compartido — `_enqueue_post_images_then`

Extraído de la parte genérica de `_enqueue_week_images` (hoy exclusiva del flujo del
mes):

```python
def _enqueue_post_images_then(post_ids: list, closing_fn, *closing_args) -> None:
    jobs = []
    for post_id in post_ids:
        post = ContentPost.objects.get(id=post_id)
        timeout = 600 if post.format == ContentPost.FORMAT_REEL else 300
        jobs.append(django_rq.enqueue(
            backfill_image_task, post_id,
            job_timeout=timeout,
            retry=Retry(max=3, interval=[10, 20, 40]),
        ))
    django_rq.enqueue(
        closing_fn, *closing_args,
        job_timeout=120,
        retry=Retry(max=2, interval=[10, 30]),
        depends_on=Dependency(jobs=jobs, allow_failure=True),
    )
```

Usa el helper de conveniencia `django_rq.enqueue(...)` (módulo, no
`get_queue('default').enqueue(...)`) — es el mismo patrón que ya usa
`_enqueue_week_images` hoy, necesario para que los tests existentes (que mockean
`patch('core.content_pipeline.tasks.django_rq')`) sigan funcionando sin cambios.

`_enqueue_week_images(calendar_id, week_index)` (mes, ya existente) se reescribe para
resolver sus 7 `post_ids` y llamar
`_enqueue_post_images_then(post_ids, _week_closing_task, calendar_id, week_index)` —
sin cambio de comportamiento observable, mismo `job_timeout`/`retry` que ya tiene hoy.

### Fase 1 — Texto (dentro de `content_generation_task`, modificado)

`content_generation_task(job_id)` deja de generar imagen/reel inline. Ahora:

1. `job.update_progress(STAGE_CONTENT, 80)` (sin cambios).
2. `TextGenerator.generate(brand_dna)` — sin cambios, auditor de marca incluido.
3. Crea el `ContentCalendar`, activa el trial (`Subscription.filter(plan__name='User')
   .update(status='trialing', ...)`) — sin cambios, ya resuelto hoy.
4. Calcula `scheduled_dates` vía `smart_schedule_dates` — sin cambios.
5. Crea los 7 `ContentPost` con `image_url=''`, `image_urls=[]`, `video_url=''` — mismo
   estado "pendiente de imagen" que `backfill_image_task` ya sabe resolver.
6. `job.update_progress(STAGE_CONTENT, 87)`.
7. Llama `_enqueue_trial_images(job_id, calendar_id, started_at=time.time())` (función
   nueva) para arrancar la Fase 2.

Manejo de error: si cualquier paso de la Fase 1 lanza excepción, se loggea y
`job.mark_failed(str(e))` — igual que hoy. La diferencia es que el camino EXITOSO ya no
marca `job.status=DONE` aquí; esa responsabilidad se mueve al cierre (Fase 2).

Timeout de este job (Fase 1): hoy se encola con `job_timeout=2400` (40 min, en
`core/brand_dna/tasks.py:71`, dimensionado para el job monolítico completo con imagen y
reel incluidos). Sin llamadas de imagen/video, baja a **300s (5 min)** — una sola
llamada a `TextGenerator.generate()` con reintentos de QC/auditor de marca, margen
amplio sobre el tiempo real medido hoy para esa sola llamada dentro del job monolítico.

### Fase 2 — Imágenes/reel en paralelo

**`_enqueue_trial_images(job_id: str, calendar_id: str, started_at: float) -> None`** —
función nueva, no es un job de RQ:

1. Resuelve los `id` de los 7 `ContentPost` del calendario
   (`calendar.posts.order_by('day_number').values_list('id', flat=True)` — el trial
   siempre crea exactamente 7, sin necesidad de calcular rango de días como en el mes).
2. Llama `_enqueue_post_images_then(post_ids, _trial_closing_task, job_id, calendar_id,
   started_at)`, donde `post_ids` es la lista de esos 7 ids (como `str`).

**`_trial_closing_task(job_id: str, calendar_id: str, started_at: float) -> None`** —
función nueva, sí es un job de RQ:

```python
def _trial_closing_task(job_id: str, calendar_id: str, started_at: float) -> None:
    job = AnalysisJob.objects.get(id=job_id)
    calendar = ContentCalendar.objects.get(id=calendar_id)
    brand_dna = calendar.brand_dna
    try:
        try:
            EmailSender().send_initial(job=job, brand_dna=brand_dna)
            schedule_daily_emails(calendar)
        except Exception as email_err:
            logger.error(f"Email inicial falló para job {job_id} (no fatal): {email_err}")
        job.stage = AnalysisJob.STAGE_COMPLETE
        job.progress = 100
        job.status = AnalysisJob.STATUS_DONE
        job.save(update_fields=['stage', 'progress', 'status'])
        CONTENT_GENERATION_DURATION.observe(time.time() - started_at)
        logger.info(f"Job {job_id} completado exitosamente")
    except Exception as e:
        CONTENT_GENERATION_DURATION.observe(time.time() - started_at)
        logger.error(f"_trial_closing_task error para job {job_id}: {e}")
        job.mark_failed(str(e))
```

Si `_trial_closing_task` mismo falla de forma inesperada (agotando sus 2 reintentos), se
trata igual que un fallo total: `job.mark_failed(...)`, en vez de dejar el job atorado
en `processing` para siempre.

### Sitio de encolado (`core/brand_dna/tasks.py:71`)

Hoy una sola línea encola `content_generation_task` O `generate_sample_task` (modo
muestra, fuera de alcance — ver "Fuera de alcance") con el mismo `job_timeout=2400`:

```python
task = content_generation_task if job.generation_mode == AnalysisJob.MODE_FULL else generate_sample_task
django_rq.enqueue(task, str(job_id), job_timeout=2400)
```

Como los dos casos ahora necesitan timeouts distintos (300s para el `content_generation_task`
rediseñado, 2400s sin cambios para `generate_sample_task` que sigue siendo monolítico),
esta línea se separa en una rama explícita por modo en vez de una sola llamada con
`job_timeout` fijo.

### Interacción con `generate_next_month` (guardia de trial ya existente)

Sin cambios: `generate_next_month` ya espera a `job.status == AnalysisJob.STATUS_DONE`
antes de generar el mes (fix del mismo día, protege contra la condición de carrera
trial/pago). Como `job.status` sigue flipando a `DONE` en el mismo punto lógico del flujo
(cuando las 7 imágenes ya están listas, solo que ahora en `_trial_closing_task` en vez de
al final de `content_generation_task`), esa guardia sigue siendo correcta sin tocarla.

## Fuera de alcance

- El análisis de marca previo a `content_generation_task` (STAGE_WEB/LOGO/POSTS) — no se
  optimiza en este spec.
- El hook del auditor de consistencia de marca en `image_generator.py` — gap confirmado,
  registrado en memoria (`project_quality_roadmap_ideas.md`, punto 9), pendiente de que
  Anuar aporte el caso real antes de diseñarlo.
- Cualquier cambio al copy o comportamiento del correo `send_initial`, o al momento en
  que se marca `job.status=DONE` — se evaluó adelantarlo a cuando el texto está listo y
  se descartó (ver Decisión #2).
- Progreso fino por imagen en la barra de `results.html` — se evaluó y se descartó (ver
  Decisión #3).
- `generate_sample_task` (genera 1 sola pieza para prospección, `MODE_SAMPLE_IMAGE`/
  `MODE_SAMPLE_REEL`) — no es un calendario de 7 posts, no aplica este patrón, no se
  toca.

## Testing

- `content_generation_task`: verificar que crea los 7 posts con `image_url=''` (no
  genera imagen inline como hoy), activa el trial (sin cambios, test ya existente),
  y encola `_enqueue_trial_images` con `started_at` como `float` reciente (mockeado).
  Caso de error en Fase 1: `job.mark_failed` se llama, no se encola nada de Fase 2.
- `_enqueue_post_images_then`: verificar que encola exactamente `len(post_ids)` jobs de
  `backfill_image_task` con el `job_timeout` correcto por formato, y 1 job de cierre con
  la `Dependency` correcta (`allow_failure=True`) sobre esos jobs — cubre tanto el caso
  de 7 posts (trial) como el de 7 posts de una semana del mes (reutilizado).
- `_enqueue_week_images` (mes, refactorizado): mismos tests ya existentes deben seguir
  pasando sin cambios de aserciones — es un refactor interno, no un cambio de
  comportamiento.
- `_enqueue_trial_images`: verifica que resuelve los 7 posts del calendario y llama a
  `_enqueue_post_images_then` con `_trial_closing_task` y los argumentos correctos.
- `_trial_closing_task`: 2 casos — éxito (manda `send_initial`, llama
  `schedule_daily_emails`, marca `job.status=DONE`/`progress=100`/`stage=COMPLETE`,
  observa la métrica de duración) y fallo interno (`job.mark_failed`, métrica también
  observada en el except).
- `backfill_image_task`: sin cambios de comportamiento, tests existentes siguen
  aplicando tal cual.
