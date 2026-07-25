# Rediseño de `content_generation_task` (semana gratis) en jobs encadenados — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `content_generation_task` (genera los 7 días de prueba gratis) deja de ser un
job monolítico que ocupa 1 worker de RQ durante ~10-15 min — se divide en una Fase 1
rápida (solo texto) y una Fase 2 de 7 jobs de imagen/reel en paralelo con un cierre
dependiente, reutilizando el mismo patrón (`Dependency` nativo de RQ) ya construido y
probado hoy para `generate_next_month`.

**Architecture:** Se extrae un helper compartido `_enqueue_post_images_then(post_ids,
closing_fn, *closing_args)` de la lógica ya existente en `_enqueue_week_images`, y se
reutiliza tanto para el flujo del mes (refactor sin cambio de comportamiento) como para
2 funciones nuevas del trial: `_enqueue_trial_images` (arranca la cadena) y
`_trial_closing_task` (manda el correo de bienvenida, agenda recordatorios diarios, y
marca el `AnalysisJob` como `DONE` — exactamente el mismo trabajo final que hacía el
job monolítico, solo que ahora corre cuando las 7 imágenes ya están listas en vez de
inline).

**Tech Stack:** Django + RQ (`django_rq`, `rq.Retry`, `rq.job.Dependency`) — mismo stack
que el resto del pipeline de contenido, sin dependencias nuevas.

## Global Constraints

- Alcance: solo `content_generation_task` (generación de contenido). El análisis de
  marca previo (`analyze_brand_task`, stages WEB/LOGO/POSTS) no se toca salvo la línea
  exacta donde encola `content_generation_task`.
- De cara al usuario: sin cambios de copy ni de UX. `send_initial` se sigue mandando
  solo cuando las 7 imágenes/reel están listas — igual que hoy.
- Barra de progreso: sin incrementos finos por imagen. Se queda en `progress=87` tras
  la Fase 1 y salta a `100` en el cierre (Fase 2). No implementar actualización de
  progreso dentro de `backfill_image_task` ni de `_enqueue_post_images_then`.
- Timeouts/retries por imagen: `job_timeout=600` para posts `format=='reel'`,
  `300` para el resto; `retry=Retry(max=3, interval=[10, 20, 40])`. Job de cierre:
  `job_timeout=120`, `retry=Retry(max=2, interval=[10, 30])`,
  `depends_on=Dependency(jobs=[...], allow_failure=True)` — valores exactos, no
  aproximar.
- El helper compartido usa `django_rq.enqueue(...)` (función de módulo), NUNCA
  `django_rq.get_queue('default').enqueue(...)` — los tests existentes mockean
  `patch('core.content_pipeline.tasks.django_rq')` y dependen de esa forma exacta.
- Fase 1 (texto) de `content_generation_task` pasa a encolarse con `job_timeout=300`
  (antes 2400) en `core/brand_dna/tasks.py`. `generate_sample_task` (modo muestra) NO
  cambia, se queda en `job_timeout=2400`.
- Métrica `CONTENT_GENERATION_DURATION`: se observa con `time.time()` (no
  `time.monotonic()`, debe ser comparable entre procesos/jobs distintos), pasado como
  argumento `started_at` a través de la cadena, observada en `_trial_closing_task`
  (ambas ramas: éxito y error interno).
- No tocar: el disparo del trial (`Subscription.filter(plan__name='User').update(...)`,
  ya resuelto hoy), `_enqueue_week_images`/`_week_closing_task` en su comportamiento
  observable (solo refactor interno), ni `generate_sample_task`.

---

### Task 1: Extraer `_enqueue_post_images_then` y refactorizar `_enqueue_week_images`

**Files:**
- Modify: `core/content_pipeline/tasks.py:252-274` (reemplaza `_enqueue_week_images`,
  agrega `_enqueue_post_images_then` antes)
- Test: `core/content_pipeline/tests/test_tasks.py` (sin cambios de contenido — este
  task es un refactor puro, los tests existentes de `_enqueue_week_images` deben seguir
  pasando sin ninguna modificación)

**Interfaces:**
- Produce: `_enqueue_post_images_then(post_ids: list, closing_fn, *closing_args) -> None`
  — función de módulo en `core/content_pipeline/tasks.py`, usada por Task 2.

Este task es un refactor puro (mismo comportamiento observable, mismas llamadas a
`django_rq.enqueue`) — no hay comportamiento nuevo que probar con un test nuevo. La
validación es que los tests YA EXISTENTES seguirán pasando sin ninguna modificación de
su código.

- [ ] **Step 1: Confirmar el código actual de `_enqueue_week_images`**

Leer `core/content_pipeline/tasks.py` líneas 252-274 y confirmar que coincide
exactamente con:

```python
def _enqueue_week_images(calendar_id: str, week_index: int) -> None:
    calendar = ContentCalendar.objects.get(id=calendar_id)
    base_day = calendar.posts.count() - 28
    week_start = base_day + (week_index * 7) + 1
    week_end = week_start + 6
    week_posts = list(
        calendar.posts.filter(day_number__gte=week_start, day_number__lte=week_end).order_by('day_number')
    )
    jobs = []
    for post in week_posts:
        timeout = 600 if post.format == ContentPost.FORMAT_REEL else 300
        job = django_rq.enqueue(
            backfill_image_task, str(post.id),
            job_timeout=timeout,
            retry=Retry(max=3, interval=[10, 20, 40]),
        )
        jobs.append(job)
    django_rq.enqueue(
        _week_closing_task, calendar_id, week_index,
        job_timeout=120,
        retry=Retry(max=2, interval=[10, 30]),
        depends_on=Dependency(jobs=jobs, allow_failure=True),
    )
```

Si difiere, DETENERSE y reportar `NEEDS_CONTEXT` — el resto de este task asume este
código exacto como punto de partida.

- [ ] **Step 2: Reemplazar por el helper compartido + `_enqueue_week_images` refactorizado**

Reemplazar el bloque completo de arriba (líneas 252-274) por:

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


def _enqueue_week_images(calendar_id: str, week_index: int) -> None:
    calendar = ContentCalendar.objects.get(id=calendar_id)
    base_day = calendar.posts.count() - 28
    week_start = base_day + (week_index * 7) + 1
    week_end = week_start + 6
    post_ids = [
        str(pid) for pid in calendar.posts.filter(
            day_number__gte=week_start, day_number__lte=week_end
        ).order_by('day_number').values_list('id', flat=True)
    ]
    _enqueue_post_images_then(post_ids, _week_closing_task, calendar_id, week_index)
```

No tocar `_week_closing_task` (líneas 277-298) — se queda exactamente igual.

- [ ] **Step 3: Correr los tests existentes de `_enqueue_week_images`/`_week_closing_task`**

Run: `docker compose exec backend pytest core/content_pipeline/tests/test_tasks.py -k "enqueue_week_images or week_closing_task" -v`

Expected: los 8 tests (`test_enqueue_week_images_enqueues_7_jobs_plus_closing`,
`test_enqueue_week_images_uses_longer_timeout_for_reel`,
`test_enqueue_week_images_selects_correct_day_range_for_week_index`,
`test_week_closing_task_week_0_sends_week_ready_and_advances`,
`test_week_closing_task_middle_weeks_silent`,
`test_week_closing_task_week_3_sends_month_ready_and_resets_flag`,
`test_week_closing_task_advances_despite_partial_failure_is_implicit_in_dependency`,
`test_week_closing_task_resets_flag_on_internal_error`) PASAN sin ninguna modificación.
Si alguno falla, es señal de que el refactor cambió comportamiento observable —
investigar antes de continuar, no ajustar el test para que pase.

- [ ] **Step 4: Correr la suite completa de `content_pipeline` para descartar regresiones**

Run: `docker compose exec backend pytest core/content_pipeline/ -v`

Expected: mismo resultado que antes del refactor (ningún test nuevo falla).

- [ ] **Step 5: Commit**

```bash
git add core/content_pipeline/tasks.py
git commit -m "refactor(trial-chunking): extrae _enqueue_post_images_then de _enqueue_week_images"
```

---

### Task 2: Nuevas funciones `_enqueue_trial_images` y `_trial_closing_task`

**Files:**
- Modify: `core/content_pipeline/tasks.py` (agregar las 2 funciones nuevas, después de
  `_week_closing_task`, antes de `_MAX_TRIAL_WAIT_ATTEMPTS`)
- Test: `core/content_pipeline/tests/test_tasks.py`

**Interfaces:**
- Consume: `_enqueue_post_images_then(post_ids, closing_fn, *closing_args)` (Task 1).
- Produce: `_enqueue_trial_images(job_id: str, calendar_id: str, started_at: float) -> None`
  y `_trial_closing_task(job_id: str, calendar_id: str, started_at: float) -> None` —
  usadas por Task 3 (`content_generation_task` llamará a `_enqueue_trial_images`).

Estas 2 funciones se prueban de forma aislada, sin tocar `content_generation_task`
todavía — se les da un calendario con 7 posts ya creados (vía fixtures existentes) y se
verifica que encolan/cierran correctamente.

- [ ] **Step 1: Escribir los tests que fallan**

Agregar a `core/content_pipeline/tests/test_tasks.py`, después de
`test_week_closing_task_resets_flag_on_internal_error` (línea 872) y antes de
`test_generate_next_month_defers_when_trial_job_not_done` (línea 875):

```python
def test_enqueue_trial_images_enqueues_7_jobs_plus_closing(calendar_with_dna):
    job_id = str(calendar_with_dna.brand_dna.job.id)
    for i in range(1, 8):
        _make_post(calendar_with_dna, i, image_url='')
    with patch('core.content_pipeline.tasks.django_rq') as mock_rq:
        mock_rq.enqueue.side_effect = lambda *a, **kw: MagicMock(spec=Job)
        from core.content_pipeline.tasks import _enqueue_trial_images
        _enqueue_trial_images(job_id, str(calendar_with_dna.id), 1234.5)
    assert mock_rq.enqueue.call_count == 8  # 7 backfill_image_task + 1 _trial_closing_task
    closing_call = mock_rq.enqueue.call_args_list[-1]
    assert closing_call.args[1:] == (job_id, str(calendar_with_dna.id), 1234.5)
    assert closing_call.kwargs['job_timeout'] == 120
    dependency = closing_call.kwargs['depends_on']
    assert isinstance(dependency, Dependency)
    assert len(dependency.dependencies) == 7
    assert dependency.allow_failure is True


def test_trial_closing_task_sends_initial_email_and_marks_job_done(job_with_dna):
    calendar = ContentCalendar.objects.create(brand_dna=job_with_dna.brand_dna)
    with patch('core.content_pipeline.tasks.EmailSender') as MockEmail, \
         patch('core.content_pipeline.tasks.schedule_daily_emails') as mock_schedule, \
         patch('core.content_pipeline.tasks.CONTENT_GENERATION_DURATION') as mock_duration:
        from core.content_pipeline.tasks import _trial_closing_task
        _trial_closing_task(str(job_with_dna.id), str(calendar.id), time.time() - 5)

    MockEmail.return_value.send_initial.assert_called_once()
    mock_schedule.assert_called_once_with(calendar)
    mock_duration.observe.assert_called_once()
    job_with_dna.refresh_from_db()
    assert job_with_dna.stage == AnalysisJob.STAGE_COMPLETE
    assert job_with_dna.progress == 100
    assert job_with_dna.status == AnalysisJob.STATUS_DONE


def test_trial_closing_task_marks_done_even_if_email_fails(job_with_dna):
    calendar = ContentCalendar.objects.create(brand_dna=job_with_dna.brand_dna)
    with patch('core.content_pipeline.tasks.EmailSender') as MockEmail, \
         patch('core.content_pipeline.tasks.schedule_daily_emails'):
        MockEmail.return_value.send_initial.side_effect = Exception('smtp down')
        from core.content_pipeline.tasks import _trial_closing_task
        _trial_closing_task(str(job_with_dna.id), str(calendar.id), time.time())

    job_with_dna.refresh_from_db()
    assert job_with_dna.status == AnalysisJob.STATUS_DONE


def test_trial_closing_task_marks_failed_on_internal_error(calendar_with_dna):
    mock_job = MagicMock()
    mock_job.save.side_effect = Exception('db down')
    with patch('core.content_pipeline.tasks.AnalysisJob.objects.get', return_value=mock_job), \
         patch('core.content_pipeline.tasks.EmailSender'), \
         patch('core.content_pipeline.tasks.schedule_daily_emails'), \
         patch('core.content_pipeline.tasks.CONTENT_GENERATION_DURATION') as mock_duration:
        from core.content_pipeline.tasks import _trial_closing_task
        _trial_closing_task('fake-job-id', str(calendar_with_dna.id), time.time())

    mock_job.mark_failed.assert_called_once()
    mock_duration.observe.assert_called_once()
```

- [ ] **Step 2: Correr los tests para confirmar que fallan**

Run: `docker compose exec backend pytest core/content_pipeline/tests/test_tasks.py -k "enqueue_trial_images or trial_closing_task" -v`

Expected: FAIL — `ImportError: cannot import name '_enqueue_trial_images'` (y lo mismo
para `_trial_closing_task`).

- [ ] **Step 3: Implementar `_enqueue_trial_images` y `_trial_closing_task`**

Agregar en `core/content_pipeline/tasks.py`, justo después de `_week_closing_task`
(después de la línea 298) y antes de `_MAX_TRIAL_WAIT_ATTEMPTS = 30`:

```python
def _enqueue_trial_images(job_id: str, calendar_id: str, started_at: float) -> None:
    calendar = ContentCalendar.objects.get(id=calendar_id)
    post_ids = [str(pid) for pid in calendar.posts.order_by('day_number').values_list('id', flat=True)]
    _enqueue_post_images_then(post_ids, _trial_closing_task, job_id, calendar_id, started_at)


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

- [ ] **Step 4: Correr los tests para confirmar que pasan**

Run: `docker compose exec backend pytest core/content_pipeline/tests/test_tasks.py -k "enqueue_trial_images or trial_closing_task" -v`

Expected: 4 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add core/content_pipeline/tasks.py core/content_pipeline/tests/test_tasks.py
git commit -m "feat(trial-chunking): agrega _enqueue_trial_images y _trial_closing_task"
```

---

### Task 3: `content_generation_task` — Fase 1 solo texto, encadena Fase 2

**Files:**
- Modify: `core/content_pipeline/tasks.py:48-128` (reescribe `content_generation_task`)
- Test: `core/content_pipeline/tests/test_tasks.py` (overhaul de los tests de
  `content_generation_task`, relocalización de 2 escenarios a nivel `backfill_image_task`)

**Interfaces:**
- Consume: `_enqueue_trial_images(job_id, calendar_id, started_at)` (Task 2).

Este task tiene el mayor volumen de cambio de tests porque `content_generation_task` ya
no genera imagen/reel inline — varios tests que hoy verifican eso a través del job
monolítico se mueven a `backfill_image_task` (que ya ejecuta ese mismo código vía
`_generate_missing_image`, compartido, sin cambios).

- [ ] **Step 1: Escribir los tests que fallan — relocalizar cobertura de reel y business_url a `backfill_image_task`**

Estos 2 escenarios hoy solo se prueban a través de `content_generation_task` (que va a
dejar de ejecutarlos inline) — `backfill_image_task` ejecuta el mismo código
(`_generate_missing_image` → `_generate_post_media`) pero hoy no tiene test propio para
reel ni para el paso de `business_url`. Agregar, después de
`test_backfill_image_task_skips_deleted_calendar` (línea 550) y antes de
`test_generate_next_month_creates_28_posts_without_images` (línea 553):

```python
def test_backfill_image_task_uses_reel_for_reel_format(calendar_with_dna):
    post = _make_post(calendar_with_dna, 1, image_url='', format='reel')
    with patch('core.content_pipeline.tasks.ImageGenerator') as MockImage, \
         patch('core.content_pipeline.tasks.ReelScriptGenerator') as MockScript, \
         patch('core.content_pipeline.tasks.ReelGenerator') as MockReel:
        MockScript.return_value.generate.return_value = {'hook_text': 'H', 'scene_prompts': ['a', 'b', 'c']}
        MockReel.return_value.generate.return_value = ('https://storage.test/reel.mp4', 'https://storage.test/poster.png')
        from core.content_pipeline.tasks import backfill_image_task
        backfill_image_task(str(post.id))

    MockImage.return_value.generate.assert_not_called()
    post.refresh_from_db()
    assert post.video_url == 'https://storage.test/reel.mp4'
    assert post.image_url == 'https://storage.test/poster.png'


def test_backfill_image_task_falls_back_to_image_when_reel_generation_fails(calendar_with_dna):
    post = _make_post(calendar_with_dna, 1, image_url='', format='reel')
    with patch('core.content_pipeline.tasks.ImageGenerator') as MockImage, \
         patch('core.content_pipeline.tasks.ReelScriptGenerator') as MockScript, \
         patch('core.content_pipeline.tasks.ReelGenerator') as MockReel:
        MockImage.return_value.generate.return_value = 'https://storage.googleapis.com/test/fallback.jpg'
        MockScript.return_value.generate.return_value = {'hook_text': 'H', 'scene_prompts': ['a', 'b', 'c']}
        MockReel.return_value.generate.return_value = ('', '')
        from core.content_pipeline.tasks import backfill_image_task
        backfill_image_task(str(post.id))

    post.refresh_from_db()
    assert post.video_url == ''
    assert post.image_url == 'https://storage.googleapis.com/test/fallback.jpg'


def test_backfill_image_task_passes_business_url_to_image_gen(calendar_with_dna):
    post = _make_post(calendar_with_dna, 3, image_url='')
    with patch('core.content_pipeline.tasks.ImageGenerator') as MockImage:
        MockImage.return_value.generate.return_value = 'https://storage.googleapis.com/test/img.jpg'
        from core.content_pipeline.tasks import backfill_image_task
        backfill_image_task(str(post.id))

    call_kwargs = MockImage.return_value.generate.call_args_list[0].kwargs
    assert call_kwargs['business_url'] == 'https://tuwebmx.com'
```

- [ ] **Step 2: Correr los tests nuevos para confirmar que YA PASAN (código no cambia todavía)**

Run: `docker compose exec backend pytest core/content_pipeline/tests/test_tasks.py -k "backfill_image_task" -v`

Expected: los 3 tests nuevos PASAN de inmediato — `backfill_image_task` no cambia en
este plan, solo se le agrega cobertura que antes vivía duplicada en
`content_generation_task`.

- [ ] **Step 3: Eliminar de `content_generation_task` los tests que quedan redundantes**

Eliminar de `core/content_pipeline/tests/test_tasks.py` estos 4 tests completos (ya
cubiertos arriba a nivel `backfill_image_task`, o redundantes porque Fase 1 ya no
genera imágenes):
- `test_content_generation_generates_image_for_every_day` (líneas 191-204)
- `test_content_generation_uses_reel_for_day_1_without_product_photo` (líneas 295-316)
- `test_content_generation_falls_back_to_image_when_reel_generation_fails` (líneas 328-347)
- `test_content_generation_passes_business_url_to_image_gen` (líneas 629-641, incluyendo
  su bloque `@override_settings` en 620-628)

También eliminar `test_content_generation_marks_job_done` (líneas 216-229) — marcar el
job como `DONE` ya no es responsabilidad de `content_generation_task`, es de
`_trial_closing_task` (ya cubierto por
`test_trial_closing_task_sends_initial_email_and_marks_job_done` del Task 2).

- [ ] **Step 4: Reescribir los tests restantes de `content_generation_task` para Fase 1**

Reemplazar el contenido de `test_content_generation_creates_calendar` (antes líneas
63-84, ajustar tras los borrados del Step 3) por:

```python
def test_content_generation_creates_calendar(job_with_dna):
    with patch('core.content_pipeline.tasks.TextGenerator') as MockText, \
         patch('core.content_pipeline.tasks._enqueue_trial_images'):
        MockText.return_value.generate.return_value = _MOCK_POSTS

        from core.content_pipeline.tasks import content_generation_task
        content_generation_task(str(job_with_dna.id))

    assert ContentCalendar.objects.filter(brand_dna__job=job_with_dna).exists()
    assert ContentPost.objects.filter(calendar__brand_dna__job=job_with_dna).count() == 7
```

Reemplazar `test_content_generation_starts_trial_for_tenant` por:

```python
def test_content_generation_starts_trial_for_tenant(job_with_dna_and_tenant):
    from core.tenant_management.models import Subscription
    with patch('core.content_pipeline.tasks.TextGenerator') as MockText, \
         patch('core.content_pipeline.tasks._enqueue_trial_images'):
        MockText.return_value.generate.return_value = _MOCK_POSTS

        from core.content_pipeline.tasks import content_generation_task
        content_generation_task(str(job_with_dna_and_tenant.id))

    sub = Subscription.objects.get(tenant=job_with_dna_and_tenant.user.tenant)
    assert sub.status == 'trialing'
    assert sub.trial_ends_at is not None
    assert sub.trial_ends_at > timezone.now() + timedelta(days=6)
    assert sub.trial_ends_at < timezone.now() + timedelta(days=8)
```

Reemplazar `test_content_generation_does_not_start_trial_for_tester_plan` (mantener el
setup de usuario/tenant/plan Tester igual, solo cambiar el bloque `with`) por:

```python
def test_content_generation_does_not_start_trial_for_tester_plan(job_with_dna):
    from django.contrib.auth import get_user_model
    from core.tenant_management.models import TenantModel, Subscription, Plan
    UserModel = get_user_model()
    tester_plan, _ = Plan.objects.get_or_create(name='Tester', defaults={
        'max_calendars_per_week': 999, 'max_post_regenerations': 999,
        'max_post_edits': 999, 'price': 0,
    })
    user = UserModel.objects.create_user(
        username='tester@test.com', email='tester@test.com', password='pass1234'
    )
    tenant = TenantModel.objects.create(name=user.email, status='active')
    Subscription.objects.create(tenant=tenant, plan=tester_plan, status='active')
    user.tenant = tenant
    user.save(update_fields=['tenant'])
    job_with_dna.user = user
    job_with_dna.save(update_fields=['user'])

    with patch('core.content_pipeline.tasks.TextGenerator') as MockText, \
         patch('core.content_pipeline.tasks._enqueue_trial_images'):
        MockText.return_value.generate.return_value = _MOCK_POSTS

        from core.content_pipeline.tasks import content_generation_task
        content_generation_task(str(job_with_dna.id))

    sub = Subscription.objects.get(tenant=tenant)
    assert sub.status == 'active'
    assert sub.trial_ends_at is None
```

Reemplazar `test_content_generation_without_user_does_not_crash` por (ya no verifica
`STATUS_DONE` — eso ahora pasa en `_trial_closing_task`, no en Fase 1):

```python
def test_content_generation_without_user_does_not_crash(job_with_dna):
    with patch('core.content_pipeline.tasks.TextGenerator') as MockText, \
         patch('core.content_pipeline.tasks._enqueue_trial_images') as mock_enqueue:
        MockText.return_value.generate.return_value = _MOCK_POSTS

        from core.content_pipeline.tasks import content_generation_task
        content_generation_task(str(job_with_dna.id))

    job_with_dna.refresh_from_db()
    assert job_with_dna.status == AnalysisJob.STATUS_PROCESSING
    mock_enqueue.assert_called_once()
```

Agregar un test nuevo, justo después del anterior, que cubre lo que antes verificaba
`test_content_generation_marks_job_done` y `test_content_generation_generates_image_for_every_day`
(ahora adaptado a que Fase 1 crea posts SIN imagen y encadena la Fase 2):

```python
def test_content_generation_creates_posts_without_images_and_enqueues_trial(job_with_dna):
    with patch('core.content_pipeline.tasks.TextGenerator') as MockText, \
         patch('core.content_pipeline.tasks._enqueue_trial_images') as mock_enqueue:
        MockText.return_value.generate.return_value = _MOCK_POSTS

        from core.content_pipeline.tasks import content_generation_task
        content_generation_task(str(job_with_dna.id))

    posts = ContentPost.objects.filter(calendar__brand_dna__job=job_with_dna)
    assert posts.count() == 7
    assert all(p.image_url == '' and p.image_urls == [] and p.video_url == '' for p in posts)

    job_with_dna.refresh_from_db()
    assert job_with_dna.stage == AnalysisJob.STAGE_CONTENT
    assert job_with_dna.progress == 87

    calendar = ContentCalendar.objects.get(brand_dna__job=job_with_dna)
    mock_enqueue.assert_called_once()
    call_args = mock_enqueue.call_args.args
    assert call_args[0] == str(job_with_dna.id)
    assert call_args[1] == str(calendar.id)
    assert isinstance(call_args[2], float)
```

Agregar un test nuevo para la rama de error de Fase 1 (no existía antes — el job
monolítico original tampoco lo tenía probado explícitamente, pero ahora que la métrica
se observa en 2 lugares distintos del código vale la pena cubrir ambos):

```python
def test_content_generation_observes_duration_metric_on_text_failure(job_with_dna):
    with patch('core.content_pipeline.tasks.TextGenerator') as MockText, \
         patch('core.content_pipeline.tasks.CONTENT_GENERATION_DURATION') as mock_duration:
        MockText.return_value.generate.side_effect = Exception('Gemini error')

        from core.content_pipeline.tasks import content_generation_task
        content_generation_task(str(job_with_dna.id))

    mock_duration.observe.assert_called_once()
    job_with_dna.refresh_from_db()
    assert job_with_dna.status == AnalysisJob.STATUS_FAILED
```

Simplificar `test_content_generation_uses_carousel_for_carousel_day` (ya no verifica
llamadas a `ImageGenerator`, solo que Fase 1 respeta el `format` que devuelve
`TextGenerator` al crear los `ContentPost`):

```python
def test_content_generation_uses_carousel_for_carousel_day(job_with_dna):
    with patch('core.content_pipeline.tasks.TextGenerator') as MockText, \
         patch('core.content_pipeline.tasks._enqueue_trial_images'):
        MockText.return_value.generate.return_value = _MOCK_POSTS_WITH_CAROUSEL

        from core.content_pipeline.tasks import content_generation_task
        content_generation_task(str(job_with_dna.id))

    posts = ContentPost.objects.filter(calendar__brand_dna__job=job_with_dna).order_by('day_number')
    carousel_post = posts.get(day_number=3)
    assert carousel_post.format == 'carousel'
    assert carousel_post.image_url == ''
    assert carousel_post.image_urls == []
    non_carousel_posts = [p for p in posts if p.day_number != 3]
    assert all(p.format == 'single' and p.image_urls == [] for p in non_carousel_posts)
```

- [ ] **Step 5: Ajustar los 3 tests de `generate_next_month` que dependen de `content_generation_task` para sembrar el trial**

`test_generate_next_month_creates_28_posts_without_images`,
`test_generate_next_month_resets_flag_on_text_failure`, y
`test_generate_next_month_keeps_flag_true_on_success` llaman a
`content_generation_task(...)` primero para crear los 7 posts del trial antes de probar
`generate_next_month`. Como Fase 1 ahora termina llamando a `_enqueue_trial_images`
(que intentaría encolar jobs reales en Redis si no se mockea), agregar
`patch('core.content_pipeline.tasks._enqueue_trial_images')` al bloque `with` de los 3.

Ejemplo para `test_generate_next_month_creates_28_posts_without_images` (mismo cambio
aplica a los otros 2 — agregar la misma línea de patch, sin tocar el resto del test):

```python
def test_generate_next_month_creates_28_posts_without_images(job_with_dna):
    from core.content_pipeline.tasks import content_generation_task, generate_next_month
    with patch('core.content_pipeline.tasks.TextGenerator') as MockText, \
         patch('core.content_pipeline.tasks.ImageGenerator') as MockImage, \
         patch('core.content_pipeline.tasks.EmailSender'), \
         patch('core.content_pipeline.tasks.schedule_daily_emails'), \
         patch('core.content_pipeline.tasks._enqueue_trial_images'), \
         patch('core.content_pipeline.tasks._enqueue_week_images') as mock_enqueue_week:
        MockText.return_value.generate.return_value = _MOCK_POSTS
        MockImage.return_value.generate.return_value = 'https://storage.googleapis.com/test/img.jpg'
        content_generation_task(str(job_with_dna.id))

        calendar = ContentCalendar.objects.get(brand_dna__job=job_with_dna)
        generate_next_month(str(calendar.id))

    posts = ContentPost.objects.filter(calendar=calendar).order_by('day_number')
    assert posts.count() == 35  # 7 del trial + 28 del mes
    day_numbers = list(posts.values_list('day_number', flat=True))
    assert day_numbers == list(range(1, 36))
    assert MockText.return_value.generate.call_count == 5  # 1 del trial + 4 del mes
    new_posts = posts.filter(day_number__gte=8)
    assert all(p.image_url == '' for p in new_posts)
    assert all(p.image_urls == [] for p in new_posts)
    assert all(p.video_url == '' for p in new_posts)
    mock_enqueue_week.assert_called_once_with(str(calendar.id), week_index=0)
```

Aplicar el mismo agregado de `patch('core.content_pipeline.tasks._enqueue_trial_images')`
(una línea más en el `with`, sin cambiar nada más) a
`test_generate_next_month_resets_flag_on_text_failure` (línea 579) y a
`test_generate_next_month_keeps_flag_true_on_success` (línea 599).

- [ ] **Step 6: Implementar la Fase 1 de `content_generation_task`**

Reemplazar la función completa (líneas 48-128 de `core/content_pipeline/tasks.py`) por:

```python
def content_generation_task(job_id: str) -> None:
    job = AnalysisJob.objects.get(id=job_id)
    brand_dna = job.brand_dna

    started_at = time.time()
    try:
        job.update_progress(AnalysisJob.STAGE_CONTENT, 80)

        text_gen = TextGenerator()
        posts_data = text_gen.generate(brand_dna)
        job.update_progress(AnalysisJob.STAGE_CONTENT, 87)

        calendar = ContentCalendar.objects.create(brand_dna=brand_dna)
        CALENDARS_CREATED.inc()
        if job.user and job.user.tenant:
            Subscription.objects.filter(tenant=job.user.tenant, plan__name='User').update(
                status='trialing',
                trial_ends_at=timezone.now() + timedelta(days=7),
            )

        now = timezone.now()
        mexico_today = now.astimezone(MEXICO_TZ).date()
        scheduled_dates = smart_schedule_dates(brand_dna, base_date=mexico_today, count=len(posts_data))

        for i, post_data in enumerate(posts_data, start=1):
            scheduled = scheduled_dates[i - 1]
            ContentPost.objects.create(
                calendar=calendar,
                day_number=i,
                caption=post_data['caption'],
                image_url='',
                image_urls=[],
                video_url='',
                format=post_data.get('format', ContentPost.FORMAT_SINGLE),
                suggested_time=scheduled.astimezone(MEXICO_TZ).time(),
                hashtags=post_data.get('hashtags', []),
                scheduled_at=scheduled,
            )

        _enqueue_trial_images(job_id, str(calendar.id), started_at)
        logger.info(f"Job {job_id}: texto listo, encadenando generación de imágenes")

    except Exception as e:
        CONTENT_GENERATION_DURATION.observe(time.time() - started_at)
        logger.error(f"content_generation_task error para job {job_id}: {e}")
        job.mark_failed(str(e))
```

La métrica se observa también en el `except` de Fase 1 (falla antes de llegar a
encolar Fase 2) para no perder la señal de fallos tempranos — el camino exitoso de
Fase 1 NO la observa (eso pasa una sola vez, al cierre, en `_trial_closing_task`,
para medir el tiempo total real hasta que el trial está completo).

Nota: `_generate_post_media`, `ImageGenerator`, `ReelScriptGenerator`, `ReelGenerator`
siguen importados al inicio del archivo (los sigue usando `_generate_missing_image`) —
no eliminar esos imports.

- [ ] **Step 7: Correr los tests para confirmar que pasan**

Run: `docker compose exec backend pytest core/content_pipeline/tests/test_tasks.py -v`

Expected: todos los tests del archivo PASAN (los de Fase 1 reescritos, los de
`backfill_image_task` con la cobertura relocalizada, los de `_enqueue_week_images`/
`_week_closing_task`/`_enqueue_trial_images`/`_trial_closing_task` del Task 1 y 2, y los
3 de `generate_next_month` ajustados en el Step 5).

- [ ] **Step 8: Commit**

```bash
git add core/content_pipeline/tasks.py core/content_pipeline/tests/test_tasks.py
git commit -m "feat(trial-chunking): content_generation_task solo genera texto, encadena imagenes"
```

---

### Task 4: Sitio de encolado — `job_timeout` distinto por modo

**Files:**
- Modify: `core/brand_dna/tasks.py:66-71`
- Test: `core/brand_dna/tests/test_tasks.py` (test que verifica el encolado de
  `content_generation_task`/`generate_sample_task`, líneas ~90-125)

**Interfaces:**
- Ninguna nueva — task de cierre, solo actualiza el `job_timeout` de encolado.

- [ ] **Step 1: Actualizar los 2 tests existentes que verifican el encolado**

En `core/brand_dna/tests/test_tasks.py`, el test `test_task_enqueues_content_generation_for_full_mode`
(líneas 83-98) es actualmente:

```python
def test_task_enqueues_content_generation_for_full_mode(pending_job):
    pending_job.generation_mode = AnalysisJob.MODE_FULL
    pending_job.save(update_fields=['generation_mode'])
    with patch('core.brand_dna.tasks.WebScraper') as MockScraper, \
         patch('core.brand_dna.tasks.ManualBrandExtractor') as MockExtractor, \
         patch('core.brand_dna.tasks.LogoAnalyzer') as MockLogo, \
         patch('core.brand_dna.tasks.django_rq') as mock_rq:
        MockScraper.return_value.fetch_context.return_value = ('texto del sitio', ['#123456'])
        MockExtractor.return_value.extract.return_value = _WEB_RESULT
        MockLogo.return_value.analyze.return_value = _LOGO_RESULT

        from core.brand_dna.tasks import analyze_brand_task
        analyze_brand_task(str(pending_job.id))

    from core.content_pipeline.tasks import content_generation_task
    assert mock_rq.enqueue.call_args.args[0] is content_generation_task
```

Agregar, como última línea de la función (después del `assert` existente):

```python
    assert mock_rq.enqueue.call_args.kwargs['job_timeout'] == 300
```

El test `test_task_enqueues_sample_generation_for_sample_mode` (líneas 109-124) es
actualmente:

```python
def test_task_enqueues_sample_generation_for_sample_mode(pending_job):
    pending_job.generation_mode = AnalysisJob.MODE_SAMPLE_REEL
    pending_job.save(update_fields=['generation_mode'])
    with patch('core.brand_dna.tasks.WebScraper') as MockScraper, \
         patch('core.brand_dna.tasks.ManualBrandExtractor') as MockExtractor, \
         patch('core.brand_dna.tasks.LogoAnalyzer') as MockLogo, \
         patch('core.brand_dna.tasks.django_rq') as mock_rq:
        MockScraper.return_value.fetch_context.return_value = ('texto del sitio', ['#123456'])
        MockExtractor.return_value.extract.return_value = _WEB_RESULT
        MockLogo.return_value.analyze.return_value = _LOGO_RESULT

        from core.brand_dna.tasks import analyze_brand_task
        analyze_brand_task(str(pending_job.id))

    from core.content_pipeline.tasks import generate_sample_task
    assert mock_rq.enqueue.call_args.args[0] is generate_sample_task
```

Agregar, como última línea de la función:

```python
    assert mock_rq.enqueue.call_args.kwargs['job_timeout'] == 2400
```

No tocar `test_task_creates_brand_dna`, `test_task_enqueues_content_generation` (línea
60-72, usa `pending_job` sin cambiar `generation_mode` — por default `MODE_FULL`, sigue
pasando igual con `job_timeout=300` aunque no lo verifique explícitamente) ni el resto
de tests del archivo.

- [ ] **Step 2: Correr los tests para confirmar que fallan**

Run: `docker compose exec backend pytest core/brand_dna/tests/test_tasks.py -v`

Expected: los 2 tests actualizados FALLAN con `AssertionError` (esperan 300/2400, el
código actual encola ambos con 2400).

- [ ] **Step 3: Implementar el cambio**

Reemplazar en `core/brand_dna/tasks.py`, dentro de `analyze_brand_task`:

```python
        from core.content_pipeline.tasks import content_generation_task, generate_sample_task
        # Genera 7 imagenes con reintentos de QC (o 1 sola pieza en modo
        # muestra) — el timeout global (360s) se queda corto. 25 min da
        # margen amplio incluso con reintentos en varios dias.
        task = content_generation_task if job.generation_mode == AnalysisJob.MODE_FULL else generate_sample_task
        django_rq.enqueue(task, str(job_id), job_timeout=2400)
```

por:

```python
        from core.content_pipeline.tasks import content_generation_task, generate_sample_task
        if job.generation_mode == AnalysisJob.MODE_FULL:
            # content_generation_task ahora solo genera texto (rápido) y encadena
            # la generación de imagen/reel en jobs paralelos — ver
            # docs/superpowers/specs/2026-07-25-trial-week-chunking-design.md
            django_rq.enqueue(content_generation_task, str(job_id), job_timeout=300)
        else:
            # generate_sample_task sigue siendo monolítico (1 sola pieza, prospección)
            django_rq.enqueue(generate_sample_task, str(job_id), job_timeout=2400)
```

- [ ] **Step 4: Correr los tests para confirmar que pasan**

Run: `docker compose exec backend pytest core/brand_dna/tests/test_tasks.py -v`

Expected: todos PASAN.

- [ ] **Step 5: Correr la suite completa del proyecto**

Run: `docker compose exec backend pytest core/ -v`

Expected: 0 fallos (mismo total de tests que antes de este plan, menos los 5 eliminados
en Task 3 + más los agregados en Tasks 2 y 3 — el número exacto no importa, lo que
importa es 0 fallos).

- [ ] **Step 6: Commit**

```bash
git add core/brand_dna/tasks.py core/brand_dna/tests/test_tasks.py
git commit -m "feat(trial-chunking): content_generation_task se encola con job_timeout=300"
```
