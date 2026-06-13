# Encuesta de fin de semana + continuación automática — Diseño

## Problema

Hoy, `content_generation_task` crea **un único** `ContentCalendar` con 7 `ContentPost` (días 1-7) y programa sus emails vía RQ. Cuando se envía el email del día 7, **no pasa nada más**: no hay señal de feedback, ni indicación de qué debe hacer el agente a continuación. Para obtener contenido nuevo, el usuario tendría que iniciar un análisis completo desde cero (re-scrapeando sitio, logo y posts), aunque el `BrandDNA` ya extraído no cambió.

## Objetivo

Al completarse cada semana (día 7, 14, 21...), el sistema:
1. Le pide al usuario feedback (rating 1-5 + comentario opcional) sobre la semana.
2. Le pregunta si quiere que se genere la siguiente semana (días 8-14, 15-21...) reutilizando el `BrandDNA` existente — sin re-scrapear nada.
3. Si dice que sí, genera y programa la siguiente semana de forma síncrona, con opción de reutilizar o renovar las imágenes de producto.
4. Si dice que no, o no responde, el ciclo termina ahí (señal de churn — sin recordatorios automáticos).

## Fuera de alcance

- **Approval learning** (usar posts aprobados como few-shot para `TextGenerator`) — roadmap separado, se conecta naturalmente con `generate_next_week` pero no se implementa aquí.
- Límite de semanas o monetización de continuaciones — generar la semana N+1 no consume el cupo `max_calendars_per_week` (no crea un `AnalysisJob` nuevo).
- Recordatorios por email si el usuario no responde la encuesta.

---

## 1. Modelo de datos

### Nuevo modelo: `WeeklyFeedback` (`core/content_pipeline/models.py`)

```python
class WeeklyFeedback(models.Model):
    CONTINUE_PENDING = 'pending'
    CONTINUE_YES = 'yes'
    CONTINUE_NO = 'no'
    CONTINUE_CHOICES = [
        (CONTINUE_PENDING, 'Pendiente'),
        (CONTINUE_YES, 'Sí'),
        (CONTINUE_NO, 'No'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    calendar = models.ForeignKey(ContentCalendar, on_delete=models.CASCADE, related_name='feedback_entries')
    week_number = models.IntegerField()  # 1, 2, 3...
    rating = models.IntegerField(null=True, blank=True)  # 1-5
    comment = models.TextField(blank=True, default='')
    continue_decision = models.CharField(max_length=10, choices=CONTINUE_CHOICES, default=CONTINUE_PENDING)
    created_at = models.DateTimeField(auto_now_add=True)
    responded_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = 'content_pipeline_weekly_feedback'
        unique_together = ('calendar', 'week_number')
        ordering = ['week_number']
```

### Campo nuevo en `ContentCalendar`

```python
active_product_images = models.JSONField(default=list, blank=True)
```

Representa el set vigente (hasta 7) de imágenes de producto usadas para generar contenido. Se inicializa en `content_generation_task` con `job.product_image_paths[:7]` (mismo valor que hoy se usa implícitamente).

---

## 2. Disparador: fin de semana

En `send_daily_email_task` (`core/content_pipeline/tasks.py`), **después** de `EmailSender().send_daily(post=post)`:

```python
if post.day_number % 7 == 0:
    week_number = post.day_number // 7
    WeeklyFeedback.objects.get_or_create(calendar=post.calendar, week_number=week_number)
```

Este es el único punto de creación de `WeeklyFeedback`. `get_or_create` evita duplicados si la tarea se reintenta.

---

## 3. Email de fin de semana

`email_daily.html` agrega un bloque condicional cuando `post.day_number` es múltiplo de 7:

```django
{% if post.day_number|divisibleby:7 %}
<div style="margin-top:24px;padding:16px;background:#1a1a2e;border-radius:8px;text-align:center;">
  <p style="color:#f0f0f0;margin:0 0 12px;">🎉 ¡Esta fue tu última pieza de esta semana!</p>
  <a href="{{ calendar_review_url }}" style="display:inline-block;padding:12px 24px;background:#e94560;color:#fff;border-radius:8px;text-decoration:none;font-weight:600;">
    Dar feedback y ver mi próxima semana →
  </a>
</div>
{% endif %}
```

`EmailSender.send_daily` construye `calendar_review_url` con `settings.COSMIC_BASE_URL` + `reverse('calendar_review', args=[post.calendar.brand_dna.job.id])`.

### Setting nuevo: `COSMIC_BASE_URL`

`FRONTEND_URL` ya existe pero apunta a `dialogix.anuarbarrera.dev` (otro proyecto). Se agrega:

```python
# settings.py
COSMIC_BASE_URL = get_env('COSMIC_BASE_URL', default='https://cosmic.anuarbarrera.dev')
```

```bash
# .env / .env.example
COSMIC_BASE_URL=https://cosmic.anuarbarrera.dev
```

El link requiere login — si el usuario no tiene sesión, Django redirige a `login` con `?next=` automáticamente (`@login_required` ya está en `calendar_review_view`).

---

## 4. Dashboard / `calendar_review_view`

```python
from core.content_pipeline.models import WeeklyFeedback

pending_feedback = None
if calendar:
    pending_feedback = calendar.feedback_entries.filter(
        continue_decision=WeeklyFeedback.CONTINUE_PENDING
    ).order_by('-week_number').first()
```

Se agrega `pending_feedback` y `product_pool` (= `job.product_image_paths`, para la galería) al contexto.

### UI en `calendar_review.html`

Si `pending_feedback` existe, se muestra un banner arriba del grid de posts:

- Título: `¡Tu semana {{ pending_feedback.week_number }} terminó! 🎉`
- Rating 1-5 (estrellas clicables, requerido)
- Textarea de comentario (opcional)
- Dos botones: **"Sí, quiero mi próxima semana"** / **"No, por ahora"**

Al hacer clic en **"Sí"**, se expande inline (sin reload) una sección **"Imágenes de producto para tu próxima semana"**:

- Radio: **"Reutilizar mis imágenes"** (default) / **"Subir nuevas imágenes"**
- Si "Reutilizar" y `product_pool|length > 7`: galería de miniaturas con checkboxes (hasta 7 seleccionables)
- Si "Reutilizar" y `product_pool|length <= 7`: sin UI adicional
- Si "Subir nuevas": `<input type="file" multiple accept="image/jpeg,image/png">` (hasta 7)

Un botón final **"Generar mi semana →"** envía el formulario completo.

---

## 5. Endpoint: `calendar_feedback_api`

`core/brand_dna/views.py`, nueva vista + URL `api/calendar/<uuid:job_id>/feedback/`:

```python
@login_required
@require_POST
def calendar_feedback_api(request, job_id):
    job = get_object_or_404(AnalysisJob, id=job_id, user=request.user)
    calendar = job.brand_dna.calendar
    feedback = get_object_or_404(
        WeeklyFeedback, calendar=calendar, continue_decision=WeeklyFeedback.CONTINUE_PENDING
    )

    feedback.rating = int(request.POST.get('rating'))
    feedback.comment = request.POST.get('comment', '')
    feedback.continue_decision = request.POST.get('continue_decision')  # 'yes' | 'no'
    feedback.responded_at = timezone.now()
    feedback.save(update_fields=['rating', 'comment', 'continue_decision', 'responded_at'])

    if feedback.continue_decision == WeeklyFeedback.CONTINUE_YES:
        next_week = feedback.week_number + 1
        _update_active_product_images(calendar, job, request, next_week)
        generate_next_week(calendar, next_week)

    return JsonResponse({'status': 'ok', 'continue_decision': feedback.continue_decision})
```

Request: `multipart/form-data` (soporta archivos cuando `continue_decision='yes'` e `image_choice='new'`).

Campos esperados cuando `continue_decision == 'yes'`:
- `image_choice`: `'reuse'` | `'new'`
- `selected_images`: lista de paths (si `reuse` y pool > 7)
- `product_images`: archivos (si `new`, hasta 7)

### `_update_active_product_images(calendar, job, request, next_week)`

```python
def _update_active_product_images(calendar, job, request, next_week):
    choice = request.POST.get('image_choice', 'reuse')
    if choice == 'new':
        files = request.FILES.getlist('product_images')[:7]
        new_paths = []
        for idx, f in enumerate(files):
            ext = f.name.rsplit('.', 1)[-1].lower() if '.' in f.name else 'jpg'
            path = f'uploads/product_{job.id}_w{next_week}_{idx}.{ext}'
            full = os.path.join(settings.MEDIA_ROOT, path)
            os.makedirs(os.path.dirname(full), exist_ok=True)
            with open(full, 'wb') as out:
                for chunk in f.chunks():
                    out.write(chunk)
            new_paths.append(path)
        if new_paths:
            job.product_image_paths = job.product_image_paths + new_paths
            job.save(update_fields=['product_image_paths'])
            calendar.active_product_images = new_paths
            calendar.save(update_fields=['active_product_images'])
    elif choice == 'reuse':
        pool = job.product_image_paths
        if len(pool) > 7:
            selected = request.POST.getlist('selected_images')[:7]
            valid = [p for p in selected if p in pool]
            if valid:
                calendar.active_product_images = valid
                calendar.save(update_fields=['active_product_images'])
        # si pool <= 7, active_product_images queda igual (ya tiene el set de la semana 1)
```

---

## 6. Generación de la siguiente semana

Nueva función en `core/content_pipeline/tasks.py` (junto a `content_generation_task`, reutiliza sus helpers):

```python
def generate_next_week(calendar: ContentCalendar, week_number: int) -> None:
    brand_dna = calendar.brand_dna
    text_gen = TextGenerator()
    posts_data = text_gen.generate(brand_dna)

    now = timezone.now()
    mexico_today = now.astimezone(MEXICO_TZ).date()
    scheduled_dates = smart_schedule_dates(brand_dna, base_date=mexico_today, count=len(posts_data))

    base_day = (week_number - 1) * 7

    for i, post_data in enumerate(posts_data, start=1):
        hour, minute = map(int, post_data.get('suggested_time', '19:00').split(':'))
        ContentPost.objects.create(
            calendar=calendar,
            day_number=base_day + i,
            caption=post_data['caption'],
            image_url='',
            suggested_time=f"{hour:02d}:{minute:02d}",
            hashtags=post_data.get('hashtags', []),
            scheduled_at=scheduled_dates[i - 1],
        )

    schedule_daily_emails(calendar)
```

Notas:
- Todas las imágenes de la nueva semana se generan de forma perezosa en `send_daily_email_task` (igual que días 2-7 de la semana 1) — no hay generación eager para el "día 1" de cada semana nueva.
- `_load_product_images(paths: list[str]) -> list[bytes]` cambia su parámetro de `job` a `paths` — ambos call sites (`content_generation_task`, `send_daily_email_task`) le pasan `calendar.active_product_images` en vez de `job.product_image_paths`. `_product_image_for_day` cambia su primer parámetro de `day_number` a `day_in_week = ((day_number - 1) % 7) + 1`. Esto preserva el comportamiento actual para la semana 1 (`day_in_week == day_number` cuando `day_number <= 7`) y aplica el mismo mapeo a semanas siguientes.

### Fix en `schedule_daily_emails`

Filtro actual:
```python
posts = list(calendar.posts.filter(day_number__gt=1).order_by('day_number'))
```

Para semana 2+, esto re-incluiría días 2-7 de la semana 1 (ya enviados) y los re-enviaría. Fix de una línea:

```python
posts = list(calendar.posts.filter(
    day_number__gt=1, status=ContentPost.STATUS_PENDING
).order_by('day_number'))
```

El día 1 de cada semana sigue excluido por `day_number__gt=1` (para semana 1, porque se envía vía `send_initial`; para semanas siguientes, `day_number` de su "día 1" es 8, 15... que es `> 1`, así que SÍ se programa — correcto, ya que no hay email inicial especial para esos días).

---

## 7. Manejo de "No" / sin respuesta

- **"No"**: se guarda `continue_decision='no'`, `responded_at=now()`. No se crea nueva `WeeklyFeedback`, no se genera nada. El calendario queda visible para revisión normal.
- **Sin respuesta**: `WeeklyFeedback` queda `pending` indefinidamente. Es la señal de churn — no hay recordatorios automáticos ni acciones adicionales.

---

## 8. Testing

- `test_models.py` (content_pipeline): constraints de `WeeklyFeedback` (`unique_together`), defaults.
- `test_tasks.py`: 
  - `send_daily_email_task` crea `WeeklyFeedback` cuando `day_number % 7 == 0`, no la crea en otros días, usa `get_or_create` (idempotente si se reintenta).
  - `generate_next_week` crea 7 `ContentPost` con `day_number` correctos (8-14 para semana 2) y los programa.
  - `_product_image_for_day` con `day_in_week` produce el mismo resultado que antes para días 1-7.
- `test_scheduler.py`: `schedule_daily_emails` no reprograma posts con `status=sent` (regresión semana 2 sobre semana 1).
- `test_email_sender.py`: el bloque de fin de semana aparece solo cuando `day_number % 7 == 0`, y `calendar_review_url` se construye con `COSMIC_BASE_URL`.
- `test_views.py` (brand_dna):
  - `calendar_review_view` expone `pending_feedback` solo cuando existe un `WeeklyFeedback` pending.
  - `calendar_feedback_api`: actualiza feedback, dispara `generate_next_week` solo si `continue_decision='yes'`, valida ownership (`user=request.user`).
  - `_update_active_product_images`: casos `reuse` (pool ≤7 sin cambios, pool >7 con selección válida), `new` (guarda archivos, actualiza pool y `active_product_images`).
