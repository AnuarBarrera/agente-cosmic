# Rediseño de `generate_next_month`: generación por semanas encadenadas

## Objetivo

`generate_next_month` (construido el 2026-07-24, mergeado a `main` local) falló en su
primera prueba end-to-end real: un solo job monolítico que genera texto+imagen para 28
días secuencialmente se cayó por timeout (`job_timeout=2400`, 40 min) después de 41
minutos, dejando el calendario a medias (24 de 35 posts) y `next_week_generating`
atorado en `True` para siempre. Este spec rediseña la ejecución para que ningún job
individual cargue con más de una semana de trabajo, entregando contenido al usuario de
forma progresiva en vez de todo-o-nada.

## Contexto — causa raíz confirmada

Investigado con datos reales de logs de la prueba fallida (no es especulación):

- `generate_next_month` hace 4x el trabajo de la función anterior `generate_next_week`
  (28 posts vs 7, texto+QC+imagen cada uno) pero el `job_timeout` se quedó igual — nunca
  se escaló junto con el volumen de trabajo. Ritmo real medido: ~46 seg/imagen, sin
  throttling del rate-limiter (el límite de Imagen 3 es 20 RPM y nunca se acercó — solo
  corría este job, sin competencia). Puro escalamiento lineal: si 7 días tardaba
  ~10-15 min, 28 días tardando 40-60+ min es esperado.
- Hallazgo secundario (agrava el diagnóstico, no es la causa): un `except Exception`
  amplio en el paso de "brand scene analysis" (`image_generator.py` ~línea 296-320)
  puede tragarse silenciosamente la excepción de timeout que RQ lanza internamente,
  seguir ejecutando como si fuera un fallback normal, y solo detenerse ~1 min después
  cuando el supervisor de RQ mata el proceso a la fuerza (`SIGKILL`) — lo que además
  impide que corra el `finally` que resetea `next_week_generating`, dejando la bandera
  atorada (mismo patrón de incidente ya documentado hace semanas con jobs huérfanos de
  RQ).
- Cada lote de 7 posts tiene exactamente **1 post de formato `reel`** (día fijo del
  pilar de contenido, `REEL_DAY` en `text_generator.py`) — el resto son `single`/
  `carousel`. El reel es la pieza más lenta (guion + Veo + Lyria + TTS + overlay), y hoy
  `_generate_post_media` lo resuelve en una sola llamada bloqueante que regresa
  `video_url` y `poster_url` juntos — no existe hoy una forma de generar la portada por
  separado, más rápido, sin correr el pipeline completo de Veo.

## Decisiones confirmadas

1. **Granularidad de trabajo: 1 job por post**, no 1 job por semana. Se reusa
   `backfill_image_task(post_id)` (ya existe, ya idempotente: `if post.image_url:
   return`, ya despacha correctamente por `post.format` incluyendo `reel`) como unidad
   atómica — sin escribir lógica de generación nueva.
2. **Encadenado con `Dependency` nativo de RQ** (RQ 2.4.0, confirmado disponible): un
   job de "cierre de semana" depende de los 7 `backfill_image_task` de esa semana y se
   dispara solo cuando terminan.
3. **Fallo parcial: la semana avanza igual.** `Dependency(jobs=[...7...],
   allow_failure=True)` — si 1 de los 7 falla de forma permanente, el cierre de semana
   dispara igual con 6/7 (o menos); el post sin imagen/video queda pendiente de
   reparación posterior, igual que hoy cuando falla un post individual.
4. **Reintentos automáticos por job**: cada `backfill_image_task` se encola con
   `Retry(max=3, interval=[10, 20, 40])` — mismos delays que `RETRY_DELAYS` en
   `core/shared/rate_limiter.py`, para consistencia.
5. **Cadencia de correos: 2 por mes**, no 4. Semana 1 lista → correo. Semanas 2 y 3 →
   avanzan en silencio. Semana 4 (mes completo) → correo. Menos ruido de bandeja, el
   usuario ya tiene contenido usable desde la semana 1.
6. **Mapeo de correos**: `send_month_ready` (ya existe, copy sin tocar — "tu mes de
   contenido está listo" ya encaja con el cierre real del mes) se reusa para el cierre
   de la **semana 4**. Un método nuevo `send_week_ready` (copy nuevo, ver abajo) es
   exclusivo para el cierre de la **semana 1**.
7. **Banner del dashboard/calendar_review: sin cambios.** Sigue mostrando "Tu próximo
   mes se está generando" (copy de hoy) durante las 4 semanas — no se rastrea progreso
   por semana en el modelo, se mantiene el booleano único `next_week_generating`. El
   usuario ya ve sus posts de la semana 1 aparecer en el calendario mismo, eso ya
   comunica que hay contenido listo.
8. **El reel no se separa en portada+video.** Se evaluó generar la portada de forma
   independiente (vía Imagen 3, más rápido) para poder mostrarla antes del video, pero
   se descartó: cambiaría la garantía actual de que la portada es un frame real del
   video final. El reel sigue siendo un solo paso atómico (como hoy), corre en
   paralelo con los 6 jobs de imagen de su semana (sin orden artificial entre ellos), y
   el cierre de semana espera los 7 por igual.
9. **Timeout por job, diferenciado por formato**: 300s para posts `single`/`carousel`,
   600s para el post `reel` de la semana — margen generoso sobre el timeout default de
   cola (360s, que hoy ya alcanza para los 7 posts de `content_generation_task`
   completos incluyendo 1 reel).
10. **El webhook de Stripe no cambia su lógica**, solo el valor de `job_timeout` al
    encolar (de 2400s a 900s — ver sección técnica). Sigue llamando
    `generate_next_month(calendar_id)` igual que hoy; el cambio de comportamiento es
    interno a esa función.

## Diseño técnico

### Fase 1 — Texto (dentro de `generate_next_month`, modificado)

`generate_next_month(calendar_id)` deja de generar imágenes. Ahora:

1. Calcula `base_day`/`base_date` igual que hoy (a partir del último post existente) y
   llama `smart_schedule_dates(brand_dna, base_date, count=28)` una vez.
2. Hace el loop de 4 batches llamando `TextGenerator.generate(brand_dna)` (igual que
   hoy, sin cambios — sigue fijo a 7 posts por llamada).
3. Crea los 28 `ContentPost` con `image_url=''`, `image_urls=[]`, `video_url=''` —
   mismo estado que un post "pendiente de imagen" que `backfill_image_task` ya sabe
   resolver.
4. Llama `schedule_daily_emails(calendar)` de inmediato — no depende de que las
   imágenes existan (`send_daily_email_task` ya tiene su propio fallback
   `_generate_missing_image` si la imagen no está lista cuando le toque enviarse).
5. Llama `_enqueue_week_images(calendar_id, week_index=0)` (función nueva, ver abajo)
   para arrancar la cadena de la semana 1.

Manejo de error: si cualquier paso de la Fase 1 lanza excepción, se loggea y
`calendar.next_week_generating` se resetea a `False` de inmediato (no hay `finally`
ciego — a diferencia de hoy, el camino exitoso NO resetea la bandera aquí, esa
responsabilidad se mueve al cierre de la semana 4).

Timeout de este job (Fase 1): **900s (15 min)** — 4 llamadas a `TextGenerator` con
reintentos de QC de seguridad, sin ninguna llamada de imagen/video.

### Fase 2 — Imágenes/reel por semana, encadenadas

**`_enqueue_week_images(calendar_id: str, week_index: int) -> None`** — función nueva,
NO es un job de RQ (solo hace llamadas `django_rq.enqueue`, rápido, se llama
directamente desde dentro de otro job):

1. Resuelve los 7 `ContentPost` de esa semana (`day_number` en el rango
   `base_day + week_index*7 + 1` .. `+7`).
2. Para cada uno, encola `backfill_image_task(post.id)` con
   `retry=Retry(max=3, interval=[10, 20, 40])` y `job_timeout=600 if post.format ==
   ContentPost.FORMAT_REEL else 300`. Guarda los 7 objetos `Job` devueltos.
3. Encola `_week_closing_task(calendar_id, week_index)` con
   `depends_on=Dependency(jobs=[...los 7...], allow_failure=True)` y
   `retry=Retry(max=2, interval=[10, 30])` (job liviano, pero con reintento propio por
   si hay un blip transitorio de Redis).

**`_week_closing_task(calendar_id: str, week_index: int) -> None`** — función nueva, SÍ
es un job de RQ:

```python
def _week_closing_task(calendar_id: str, week_index: int) -> None:
    calendar = ContentCalendar.objects.get(id=calendar_id)
    brand_dna = calendar.brand_dna
    try:
        if week_index == 0:
            try:
                EmailSender().send_week_ready(job=brand_dna.job, brand_dna=brand_dna)
            except Exception as email_err:
                logger.error(f"Email de semana 1 lista falló para calendar {calendar_id} (no fatal): {email_err}")
        if week_index < 3:
            _enqueue_week_images(calendar_id, week_index + 1)
        else:
            try:
                EmailSender().send_month_ready(job=brand_dna.job, brand_dna=brand_dna)
            except Exception as email_err:
                logger.error(f"Email de mes listo falló para calendar {calendar_id} (no fatal): {email_err}")
            calendar.next_week_generating = False
            calendar.save(update_fields=['next_week_generating'])
    except Exception as e:
        logger.error(f"_week_closing_task error para calendar {calendar_id}, semana {week_index}: {e}")
        calendar.next_week_generating = False
        calendar.save(update_fields=['next_week_generating'])
```

Si `_week_closing_task` mismo falla de forma inesperada (agotando sus 2 reintentos), la
cadena se rompe ahí — se trata igual que un fallo total: se loggea y se resetea la
bandera, en vez de dejar al usuario viendo el banner de "generando" para siempre.

### Correos — copy

`send_month_ready`: **sin cambios**, se reusa tal cual para el cierre de la semana 4
(mes completo).

`send_week_ready` (nuevo método en `EmailSender`, mismo patrón que sus hermanos —
`render_to_string` + `send_mail` + `EMAILS_SENT.labels(...).inc()`): copy borrador
sujeto a revisión de Anuar antes de implementar —

> Asunto: "🎉 Tu primera semana de contenido ya está lista"
> Cuerpo: "Ya puedes revisar y descargar tus primeros 7 días — seguimos generando el
> resto del mes en segundo plano, te avisamos apenas esté completo."

### Webhook de Stripe

Único cambio: `django_rq.enqueue(generate_next_month, str(calendar.id),
job_timeout=900)` — antes `job_timeout=2400`. El resto de `stripe_views.py` no se toca.

## Fuera de alcance

- No se toca el resto del plan mensual del 2026-07-24 (gate de pago, CTA temprano,
  copy sin "suscripción", retiro de `WeeklyFeedback`) — todo eso se da por bueno y
  estable.
- No se separa la portada del reel de su video (decisión #8 arriba) — queda como
  mejora futura si algún día se necesita.
- No se prioriza el trabajo de imágenes de TODO el mes por delante de los reels
  (se evaluó y se descartó — el orden es estrictamente semana por semana, secuencial).
- La limpieza del tenant de prueba roto por la corrida fallida de hoy
  (`ventas@anuarbarrera.dev`, tenant `54d0c749-fb2a-46b5-a289-5d1a970cbe50`: 24/35
  posts, `next_week_generating` atorado en `True`, job `f6606e34-9db5-4e7a-9395-
  3529646ac79a` en el `FailedJobRegistry`) es un paso operativo aparte, después de
  tener este plan implementado — no se asume que ya está limpio.

## Testing

- `generate_next_month`: verificar que crea los 28 posts con `image_url=''` (no genera
  imagen inline como hoy), llama `schedule_daily_emails`, y encola `_enqueue_week_images`
  para `week_index=0` (mockeado). Caso de error: la bandera se resetea.
- `_enqueue_week_images`: verificar que encola exactamente 7 `backfill_image_task` con
  el `job_timeout` correcto por formato (300 vs 600 para el post reel de esa semana) y
  1 `_week_closing_task` con la `Dependency` correcta (los 7 jobs, `allow_failure=True`).
- `_week_closing_task`: 3 casos — `week_index=0` (manda `send_week_ready`, encola
  semana 2, NO resetea bandera), `week_index=1` o `2` (silencioso, solo encola la
  siguiente), `week_index=3` (manda `send_month_ready`, resetea bandera a `False`, NO
  encola nada más). Caso de error interno: resetea bandera.
- `backfill_image_task`: sin cambios de comportamiento, tests existentes siguen
  aplicando tal cual.
