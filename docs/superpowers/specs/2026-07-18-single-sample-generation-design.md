# Generación de muestra individual (imagen o reel) para prospección — diseño

## Contexto

Anuar va a salir a prospectar ofreciendo el servicio y necesita mandar contenido
de muestra (una imagen o un reel) generado a partir de la marca de cada
prospecto, sin comprometerse al calendario completo de 7 días que hoy es el
único resultado posible del formulario de análisis. No sabe de antemano si el
reel o la imagen va a generar más impacto por prospecto, así que quiere poder
elegir el formato caso por caso.

Hoy, `analyze_brand_task` (análisis de marca) siempre encadena automáticamente
a `content_generation_task` (calendario completo de 7 días + 7 correos
programados) al terminar — no existe forma de detenerse solo en el análisis
ni de pedir un único post en el formato elegido.

## Decisión de diseño

Se agrega la capacidad de elegir, en el mismo formulario `/analizar/`, entre:
"Calendario completo (7 días)" (el comportamiento de hoy, sin cambios),
"Solo 1 imagen de muestra", o "Solo 1 reel de muestra". Esta capacidad está
controlada por un permiso a **nivel de Plan** (no un chequeo de rol
hardcodeado), reutilizando el mismo mecanismo que ya limita
`max_calendars_per_week`/`max_post_regenerations`/etc. por plan — así,
activarla para el plan Tester o para un plan de pago específico en el futuro
es cambiar un campo desde el admin de Django, sin tocar código. Hoy se activa
únicamente en el Plan Admin.

El formulario, para usuarios sin el permiso, se ve exactamente igual que hoy
— el selector de modo no se renderiza en absoluto. El backend revalida el
permiso siempre (nunca confía en el valor recibido del navegador): si llega
un modo distinto a "completo" de un usuario sin el permiso, se ignora y se
procesa como calendario completo.

## Arquitectura

### `Plan` — nuevo campo

`core/tenant_management/models.py`: `allows_sample_generation =
models.BooleanField(default=False)`. Migración con `default=False` para
todos los planes existentes; se activa manualmente en el Plan Admin después
de la migración (no vía data migration — es una decisión de Anuar, no algo
automático).

### `AnalysisJob` — nuevo campo

`core/brand_dna/models.py`: `generation_mode = models.CharField(max_length=20,
choices=[('full', 'Calendario completo'), ('sample_image', 'Muestra: imagen'),
('sample_reel', 'Muestra: reel')], default='full')`. Guarda qué se pidió, para
que la tarea asíncrona (que corre después, sin acceso directo al request) sepa
qué generar al terminar el análisis.

### Formulario (`new_analysis.html`) — selector condicional

El view que renderiza el formulario (`GET` de `new_analysis`, ya existe en
`core/brand_dna/views.py`) pasa al contexto `allows_sample_generation =
get_user_plan(request.user).allows_sample_generation` (reutiliza
`get_user_plan` de `core/brand_dna/rate_limits.py`, ya usado para los límites
existentes). El template renderiza 3 `<input type="radio" name="generation_mode">`
(`full` marcado por default, `sample_image`, `sample_reel`) **solo si**
`allows_sample_generation` es verdadero — para todos los demás usuarios, el
HTML de esos radios ni se genera.

### `analyze_submit` — validación server-side y bifurcación

`core/brand_dna/views.py::analyze_submit`: lee
`request.POST.get('generation_mode', 'full')`. Si el valor no está en
`{'full', 'sample_image', 'sample_reel'}`, o si `get_user_plan(request.user)
.allows_sample_generation` es falso, se fuerza a `'full'` — nunca se confía en
el valor recibido para los modos restringidos. El valor final validado se
guarda en `AnalysisJob.generation_mode` al crear el job (línea ya existente
`AnalysisJob.objects.create(...)`).

### `analyze_brand_task` — bifurca al final en vez de encadenar siempre igual

`core/brand_dna/tasks.py`: el último bloque (hoy: `django_rq.enqueue(
content_generation_task, str(job_id), job_timeout=2400)`, incondicional) pasa
a revisar `job.generation_mode`:
- `'full'` (default): comportamiento actual, sin cambios.
- `'sample_image'` / `'sample_reel'`: encola una tarea nueva,
  `generate_sample_task`, con el mismo `job_timeout=2400` (la generación de
  un solo reel puede tardar varios minutos igual que hoy).

### `generate_sample_task` (nueva) — genera 1 solo post, sin calendario completo

`core/content_pipeline/tasks.py`: nueva función, misma forma que
`content_generation_task` pero con una sola iteración:

```python
def generate_sample_task(job_id: str) -> None:
    job = AnalysisJob.objects.get(id=job_id)
    brand_dna = job.brand_dna
    try:
        job.update_progress(AnalysisJob.STAGE_CONTENT, 80)

        text_gen = TextGenerator()
        posts_data = text_gen.generate(brand_dna)
        job.update_progress(AnalysisJob.STAGE_CONTENT, 87)

        # TextGenerator ya fija el formato por posicion (dia 1 = reel,
        # ver REEL_DAY en text_generator.py) — dia 0 del array (indice 0)
        # es reel, dia 1 (indice 1) es imagen individual. No se toca
        # TextGenerator para esto, se reutiliza tal cual.
        wanted_format = ContentPost.FORMAT_REEL if job.generation_mode == 'sample_reel' else ContentPost.FORMAT_SINGLE
        post_data = next(p for p in posts_data if p.get('format') == wanted_format)

        calendar = ContentCalendar.objects.create(brand_dna=brand_dna)
        image_gen = ImageGenerator(bucket_name=settings.GOOGLE_CLOUD_STORAGE_BUCKET)
        reel_script_gen = ReelScriptGenerator()
        reel_gen = ReelGenerator(bucket_name=settings.GOOGLE_CLOUD_STORAGE_BUCKET)

        image_url, image_urls, video_url = _generate_post_media(
            image_gen, reel_script_gen, reel_gen,
            fmt=wanted_format,
            filename=f"{job_id}-sample",
            caption=post_data['caption'],
            colors=brand_dna.primary_colors,
            tone=brand_dna.tone,
            brand_name=brand_dna.business_name,
            keywords=brand_dna.keywords,
            description=brand_dna.description,
            audience=brand_dna.audience,
            business_url=brand_dna.business_url,
            brand_dna=brand_dna,
            post_data=post_data,
        )

        ContentPost.objects.create(
            calendar=calendar,
            day_number=1,
            caption=post_data['caption'],
            image_url=image_url,
            image_urls=image_urls,
            video_url=video_url,
            format=wanted_format,
            suggested_time='09:00',
            hashtags=post_data.get('hashtags', []),
            scheduled_at=timezone.now(),
        )

        job.stage = AnalysisJob.STAGE_COMPLETE
        job.progress = 100
        job.status = AnalysisJob.STATUS_DONE
        job.save(update_fields=['stage', 'progress', 'status'])
        logger.info(f"Muestra generada para job {job_id} ({wanted_format})")

    except Exception as e:
        logger.error(f"generate_sample_task error para job {job_id}: {e}")
        job.mark_failed(str(e))
```

**Deliberadamente NO incluye** (a diferencia de `content_generation_task`):
`EmailSender().send_initial(...)` ni `schedule_daily_emails(calendar)` — no
tiene sentido programar recordatorios diarios para una muestra de un solo
post que no corresponde a un calendario real de publicación.

### Entrega — reutiliza `calendar_review.html` sin cambios

El resultado se ve en la misma pantalla `calendar_review.html` ya existente
(ruta `/calendar/<job_id>/`), sin ningún cambio de template — con 1 sola
tarjeta en vez de 7. Ya incluye el botón de descarga y, si es reel, el aviso
de "súbelo como Reel/Story, no como post normal" agregado en HALLAZGO 75.

## Fuera de alcance

- No se toca `TextGenerator` — se reutiliza tal cual, incluida su lógica de
  QC de captions (`_ensure_safe_caption`) y su costo (genera igual las 7
  ideas de contenido en una sola llamada a Gemini, aunque solo se use 1 —
  el costo de texto es marginal frente al de imagen/video).
- No se activa el permiso para ningún otro Plan en esta implementación —
  solo el Plan Admin. Activar para Tester o un plan de pago es un cambio de
  configuración posterior, explícitamente fuera de este plan.
- No se agrega ninguna forma de generar una muestra para un prospecto que NO
  tenga una cuenta de Cosmic — la muestra se genera bajo la cuenta del
  usuario Admin que la solicita (mismo `AnalysisJob.user`), igual que
  cualquier análisis hoy.
- No se modifica `can_create_calendar`/el límite de `max_calendars_per_week`
  — una muestra individual sigue contando como 1 `AnalysisJob` más para ese
  límite, sin caso especial.

## Testing

Mismo patrón ya establecido en `core/brand_dna/tests/test_views.py` y
`core/content_pipeline/tests/test_tasks.py`:
- `analyze_submit`: el selector de modo se ignora/fuerza a `'full'` cuando el
  plan del usuario no tiene el permiso, incluso si se manda
  `generation_mode=sample_reel` directo en el POST. Con el permiso activo, el
  valor se guarda correctamente en el `AnalysisJob` creado.
- `new_analysis` (GET): el contexto expone `allows_sample_generation`
  correctamente según el plan; test de que el HTML no contiene los radios
  cuando es falso.
- `analyze_brand_task`: con `generation_mode='full'` encola
  `content_generation_task` (comportamiento actual, ya cubierto por tests
  existentes — confirmar que siguen pasando sin cambios); con
  `generation_mode='sample_image'`/`'sample_reel'` encola
  `generate_sample_task` en vez de `content_generation_task`.
- `generate_sample_task`: crea exactamente 1 `ContentPost` en un
  `ContentCalendar` nuevo, con el formato correcto según el modo, sin llamar
  a `EmailSender`/`schedule_daily_emails` (mockeados y verificados con
  `assert_not_called()`).

## Verificación real

Al final del plan: generar 1 muestra real de imagen y 1 muestra real de reel
de punta a punta (activando el permiso en el Plan Admin local), confirmar que
`calendar_review.html` muestra correctamente 1 sola tarjeta con descarga
funcional, y que no se dispara ningún correo.
