# Magic Link (auto-login desde correo) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Que el usuario que abre un correo de Agente Cosmic entre directo a su calendario sin que le pidan contraseña, incluso desde el navegador embebido de Gmail en el celular.

**Architecture:** Un modelo `LoginToken` (espejo del `PasswordResetToken` ya existente) guarda un token de 256 bits con su destino y una ventana de 72 horas. Una vista bajo `/auth/` valida el token, ejecuta `login()` y redirige al destino guardado en la fila. `EmailSender` genera un token nuevo por cada correo enviado, con fail-open si la creación falla.

**Tech Stack:** Django 5.2, PostgreSQL, pytest, prometheus_client, Docker Compose.

**Spec:** `docs/superpowers/specs/2026-08-23-magic-link-design.md`

## Global Constraints

- Commits: `GIT_EDITOR=true git commit -m "msg"` — nunca heredoc, nunca `-A`/`-a`. Se hace `git add` de los archivos exactos listados en cada tarea.
- Tests: `docker compose exec -T backend python -m pytest <path> -q`. La suite completa está en 817/817 al empezar este plan.
- Tras tocar `email_sender.py`: `docker compose up -d --force-recreate --no-deps backend rqworker`. No basta `restart`, y `rqworker` también debe recrearse, no solo `backend`.
- Vigencia del token: **72 horas**, reutilizable (sin `is_used`).
- Entropía del token: `secrets.token_urlsafe(32)`.
- Rate limit: **10 intentos fallidos por IP en 5 minutos** (300 segundos). Los accesos exitosos no incrementan el contador.
- El campo `redirect_to` guarda **solo rutas relativas** del propio dominio. Nunca una URL absoluta — eso es lo que cierra el open redirect.
- El árbol tiene ruido preexistente sin trackear que **no se toca ni se commitea**: `consolidado.md`, `cambiosUI.md`, `cambiosNanoBanana.md`, `hallazgosReel.md`, `.cybersec-context.md`, `nginx.dev.conf`, scaffolding de `hyperframes_reel`, `node_modules`, `core/content_pipeline/management/commands/test_product_reference_pipeline.py`, y los `.md` borrados sin stage (`PENDIENTES.md`, `geminiAnalisis.md`, `hallazgosImagen.txt`, `migracionDeModelo.txt`).
- Los 7 templates HTML de correo **no se modifican** en ninguna tarea.

---

## File Structure

| Archivo | Responsabilidad | Tarea |
|---|---|---|
| `core/tenant_management/models.py` | `generate_login_token()` + modelo `LoginToken` | 1 |
| `core/tenant_management/migrations/0028_logintoken.py` | Migración del modelo | 1 |
| `core/tenant_management/tests/test_models.py` | Tests del modelo | 1 |
| `core/shared/metrics.py` | Contador `MAGIC_LOGINS` | 2 |
| `core/brand_dna/auth_views.py` | `magic_login_view` + rate limit | 2 |
| `core/brand_dna/urls.py` | Ruta `auth/entrar/<str:token>/` | 2 |
| `core/brand_dna/tests/test_auth_views.py` | Tests de la vista | 2 |
| `core/content_pipeline/email_sender.py` | Helper `_magic_url` + 7 call sites | 3 |
| `core/content_pipeline/tests/test_email_sender.py` | Tests de integración de correos | 3 |
| `core/tenant_management/management/commands/purge_login_tokens.py` | Purga de expirados | 4 |
| `core/tenant_management/tests/test_purge_login_tokens_command.py` | Tests del comando | 4 |

---

## Desviación del spec, decidida al leer el código real

El spec describe el fail-open de `_magic_url` como un `try/except Exception` genérico. Al leer el código real apareció un caso que **no es excepcional sino esperado y frecuente**: `AnalysisJob.user` es nullable (`core/brand_dna/models.py:52-57`, `on_delete=models.SET_NULL`), y los fixtures existentes de `test_email_sender.py` crean jobs sin user.

Si `user` llega como `None`, crear el `LoginToken` lanzaría `IntegrityError`, el `except` lo atraparía y `logger.exception` escribiría un stack trace completo por cada correo de un job sin usuario. Eso convierte un caso normal en ruido de logs.

**Por eso `_magic_url` lleva un guard explícito de `user is None` que retorna el link normal sin intentar crear el token ni loguear nada.** El `try/except` sigue existiendo para los fallos genuinos de base de datos.

---

## Task 1: Modelo `LoginToken`

**Files:**
- Modify: `core/tenant_management/models.py` (agregar `generate_login_token` junto a `generate_reset_token` en línea ~26, y la clase `LoginToken` después de `PasswordResetToken` que termina en línea ~222)
- Create: `core/tenant_management/migrations/0028_logintoken.py` (generada por Django)
- Test: `core/tenant_management/tests/test_models.py`

**Interfaces:**
- Consumes: nada (primera tarea).
- Produces: `LoginToken` con campos `user` (FK a `settings.AUTH_USER_MODEL`), `token` (str), `redirect_to` (str), `created_at`, `expires_at`, `used_count` (int), `last_used_at`, `last_used_ip`; métodos `is_expired() -> bool` e `is_valid() -> bool`. Función `generate_login_token() -> str`.

- [ ] **Step 1: Escribir los tests que fallan**

Agregar al final de `core/tenant_management/tests/test_models.py`:

```python
def test_login_token_expira_a_las_72_horas():
    """La ventana de 72h es la decisión central del spec — si alguien la cambia
    por accidente, este test lo atrapa."""
    from django.utils import timezone
    from core.tenant_management.models import LoginToken
    user = User.objects.create_user(email='magic@ejemplo.com', password=_TEST_PWD)

    antes = timezone.now()
    tok = LoginToken.objects.create(user=user, redirect_to='/dashboard/')
    despues = timezone.now()

    esperado_min = antes + timezone.timedelta(hours=72)
    esperado_max = despues + timezone.timedelta(hours=72)
    assert esperado_min <= tok.expires_at <= esperado_max


def test_login_token_es_valido_antes_de_expirar():
    from django.utils import timezone
    from core.tenant_management.models import LoginToken
    user = User.objects.create_user(email='magic2@ejemplo.com', password=_TEST_PWD)

    tok = LoginToken.objects.create(
        user=user, redirect_to='/dashboard/',
        expires_at=timezone.now() + timezone.timedelta(minutes=1),
    )

    assert tok.is_expired() is False
    assert tok.is_valid() is True


def test_login_token_expirado_no_es_valido():
    from django.utils import timezone
    from core.tenant_management.models import LoginToken
    user = User.objects.create_user(email='magic3@ejemplo.com', password=_TEST_PWD)

    tok = LoginToken.objects.create(
        user=user, redirect_to='/dashboard/',
        expires_at=timezone.now() - timezone.timedelta(seconds=1),
    )

    assert tok.is_expired() is True
    assert tok.is_valid() is False


def test_login_token_no_tiene_is_used():
    """A diferencia de PasswordResetToken y EmailVerificationToken, este token
    es REUTILIZABLE dentro de su ventana (decisión del spec: el prefetch de
    Gmail quemaría un token de un solo uso antes del clic del usuario).
    used_count reemplaza al booleano."""
    from core.tenant_management.models import LoginToken
    campos = {f.name for f in LoginToken._meta.get_fields()}
    assert 'is_used' not in campos
    assert 'used_count' in campos


def test_login_token_arranca_con_used_count_en_cero():
    from core.tenant_management.models import LoginToken
    user = User.objects.create_user(email='magic4@ejemplo.com', password=_TEST_PWD)

    tok = LoginToken.objects.create(user=user, redirect_to='/dashboard/')

    assert tok.used_count == 0
    assert tok.last_used_at is None
    assert tok.last_used_ip is None


def test_login_token_genera_tokens_unicos_y_largos():
    from core.tenant_management.models import LoginToken
    user = User.objects.create_user(email='magic5@ejemplo.com', password=_TEST_PWD)

    t1 = LoginToken.objects.create(user=user, redirect_to='/dashboard/')
    t2 = LoginToken.objects.create(user=user, redirect_to='/dashboard/')

    assert t1.token != t2.token
    # secrets.token_urlsafe(32) produce ~43 caracteres
    assert len(t1.token) >= 40
```

`test_models.py` hoy solo importa `pytest`, `timezone` y tres modelos de `tenant_management` — **no** tiene `User` ni `_TEST_PWD`. Agregar al inicio del archivo, después de la línea 3:

```python
import secrets
from django.contrib.auth import get_user_model

User = get_user_model()
# Contraseña generada dinámicamente — el repo no hardcodea contraseñas de prueba
_TEST_PWD = f"T3st-{secrets.token_urlsafe(10)}!"
```

- [ ] **Step 2: Correr los tests para verificar que fallan**

Run: `docker compose exec -T backend python -m pytest core/tenant_management/tests/test_models.py -q -k login_token`
Expected: FAIL con `ImportError: cannot import name 'LoginToken'`

- [ ] **Step 3: Agregar `generate_login_token`**

En `core/tenant_management/models.py`, justo después de `generate_reset_token` (línea ~28):

```python
def generate_login_token():
    """Generate a secure token for magic link auto-login"""
    return secrets.token_urlsafe(32)
```

- [ ] **Step 4: Agregar el modelo `LoginToken`**

En `core/tenant_management/models.py`, inmediatamente después de la clase `PasswordResetToken` (que termina con su `class Meta`, línea ~222) y antes de `class BlacklistedToken`:

```python
class LoginToken(models.Model):
    """Token de auto-login enviado por correo (magic link).

    A diferencia de EmailVerificationToken y PasswordResetToken, este token es
    REUTILIZABLE dentro de su ventana de 72h: un token de un solo uso lo
    quemaría el prefetch de Gmail/Outlook antes de que el usuario haga clic, y
    rompería el caso "lo abro en el celular y luego en la computadora".
    Por eso no lleva is_used — used_count registra los accesos para auditoría.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    token = models.CharField(max_length=64, unique=True, default=generate_login_token)
    # Ruta RELATIVA del propio dominio (ej. '/calendar/<uuid>/'). Guardar el
    # destino del lado del servidor —en vez de aceptar un ?next= en la URL—
    # es lo que cierra el open redirect.
    redirect_to = models.CharField(max_length=255)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    used_count = models.IntegerField(default=0)
    last_used_at = models.DateTimeField(null=True, blank=True)
    last_used_ip = models.GenericIPAddressField(null=True, blank=True)

    def save(self, *args, **kwargs):
        if not self.expires_at:
            # Token válido por 72 horas
            self.expires_at = timezone.now() + timezone.timedelta(hours=72)
        super().save(*args, **kwargs)

    def is_expired(self):
        return timezone.now() > self.expires_at

    def is_valid(self):
        return not self.is_expired()

    class Meta:
        db_table = 'login_tokens'
        verbose_name = 'Login Token'
        verbose_name_plural = 'Login Tokens'
```

Nota: el archivo ya importa `uuid`, `secrets`, `timezone` y `models` en su cabecera, y `User` está definido en el propio módulo (es el `AbstractUser` del proyecto). No agregar imports nuevos.

- [ ] **Step 5: Generar la migración**

Run: `docker compose exec -T backend python manage.py makemigrations tenant_management`
Expected: crea `core/tenant_management/migrations/0028_logintoken.py`

Verificar que el archivo generado se llame `0028_logintoken.py` (la última existente es `0027_plan_stripe_payment_link_url.py`). Si Django le pone otro sufijo, mantener el que genere — lo importante es que sea `0028_`.

- [ ] **Step 6: Aplicar la migración**

Run: `docker compose exec -T backend python manage.py migrate tenant_management`
Expected: `Applying tenant_management.0028_logintoken... OK`

- [ ] **Step 7: Correr los tests para verificar que pasan**

Run: `docker compose exec -T backend python -m pytest core/tenant_management/tests/test_models.py -q -k login_token`
Expected: PASS, 6 tests

- [ ] **Step 8: Correr la suite del módulo**

Run: `docker compose exec -T backend python -m pytest core/tenant_management/ -q`
Expected: PASS, sin regresiones

- [ ] **Step 9: Commit**

```bash
git add core/tenant_management/models.py core/tenant_management/migrations/0028_logintoken.py core/tenant_management/tests/test_models.py
GIT_EDITOR=true git commit -m "feat(tenant_management): modelo LoginToken para magic links de 72h reutilizables"
```

---

## Task 2: Vista `magic_login_view`, ruta y métrica

**Files:**
- Modify: `core/shared/metrics.py` (agregar `MAGIC_LOGINS` después de `EMAIL_VERIFICATIONS`, línea ~67)
- Modify: `core/brand_dna/auth_views.py` (agregar constantes de rate limit y la vista; importar `MAGIC_LOGINS` en el bloque de imports de línea 14-17)
- Modify: `core/brand_dna/urls.py` (agregar ruta después de la línea 28)
- Test: `core/brand_dna/tests/test_auth_views.py`

**Interfaces:**
- Consumes: `LoginToken` de Task 1 (campos `token`, `redirect_to`, `used_count`, `last_used_at`, `last_used_ip`; método `is_valid()`).
- Produces: ruta con `name='magic_login'` que acepta un argumento posicional `token` (str). Task 3 la usa vía `reverse('magic_login', args=[tok.token])`.

- [ ] **Step 1: Escribir los tests que fallan**

Agregar al final de `core/brand_dna/tests/test_auth_views.py`:

```python
# ── Magic link (auto-login desde correo) ──

@pytest.fixture
def magic_user(db):
    from core.tenant_management.models import LoginToken  # noqa: F401
    user = User.objects.create_user(email='magiclink@ejemplo.com', password=_TEST_PWD)
    _make_tenant(user)
    return user


def test_magic_link_valido_loguea_y_redirige_al_destino(client, magic_user):
    from core.tenant_management.models import LoginToken
    tok = LoginToken.objects.create(user=magic_user, redirect_to='/dashboard/')

    resp = client.get(f'/auth/entrar/{tok.token}/')

    assert resp.status_code == 302
    assert resp.url == '/dashboard/'
    assert client.session.get('_auth_user_id') == str(magic_user.id)


def test_magic_link_registra_el_uso(client, magic_user):
    """used_count/last_used_ip son para auditoría forense: permiten responder
    'desde qué IPs se usó este token' si el usuario reporta algo raro."""
    from core.tenant_management.models import LoginToken
    tok = LoginToken.objects.create(user=magic_user, redirect_to='/dashboard/')

    client.get(f'/auth/entrar/{tok.token}/', HTTP_X_REAL_IP='203.0.113.7')

    tok.refresh_from_db()
    assert tok.used_count == 1
    assert tok.last_used_at is not None
    assert tok.last_used_ip == '203.0.113.7'


def test_magic_link_es_reutilizable_dentro_de_la_ventana(client, magic_user):
    """Decisión del spec: reutilizable para sobrevivir al prefetch de Gmail y
    al uso en dos dispositivos."""
    from core.tenant_management.models import LoginToken
    tok = LoginToken.objects.create(user=magic_user, redirect_to='/dashboard/')

    primera = client.get(f'/auth/entrar/{tok.token}/')
    client.logout()
    segunda = client.get(f'/auth/entrar/{tok.token}/')

    assert primera.status_code == 302
    assert segunda.status_code == 302
    assert segunda.url == '/dashboard/'
    assert client.session.get('_auth_user_id') == str(magic_user.id)
    tok.refresh_from_db()
    assert tok.used_count == 2


def test_magic_link_expirado_manda_a_login_con_next(client, magic_user):
    """El ?next= es seguro porque sale de la BD, no de la URL: tras poner su
    contraseña el usuario cae donde iba, no en el dashboard genérico."""
    from django.utils import timezone
    from core.tenant_management.models import LoginToken
    tok = LoginToken.objects.create(
        user=magic_user, redirect_to='/calendar/abc/',
        expires_at=timezone.now() - timezone.timedelta(seconds=1),
    )

    resp = client.get(f'/auth/entrar/{tok.token}/')

    assert resp.status_code == 302
    assert resp.url.startswith('/auth/login/')
    assert 'next=%2Fcalendar%2Fabc%2F' in resp.url or 'next=/calendar/abc/' in resp.url
    assert client.session.get('_auth_user_id') is None


def test_magic_link_inexistente_manda_a_login_sin_next(client, db):
    resp = client.get('/auth/entrar/token-que-no-existe/')

    assert resp.status_code == 302
    assert resp.url.startswith('/auth/login/')
    assert 'next=' not in resp.url
    assert client.session.get('_auth_user_id') is None


def test_magic_link_no_revive_cuenta_desactivada(client, magic_user):
    """Una cuenta desactivada se reactiva por auth/reactivate/, nunca por
    magic link."""
    from core.tenant_management.models import LoginToken
    magic_user.is_active = False
    magic_user.save(update_fields=['is_active'])
    tok = LoginToken.objects.create(user=magic_user, redirect_to='/dashboard/')

    resp = client.get(f'/auth/entrar/{tok.token}/')

    assert resp.status_code == 302
    assert resp.url.startswith('/auth/login/')
    assert client.session.get('_auth_user_id') is None


def test_magic_link_reemplaza_sesion_de_otro_usuario(client, magic_user):
    """Computadora compartida: si había sesión de otro, el magic link la
    reemplaza correctamente (Django cicla la sesión en login())."""
    from core.tenant_management.models import LoginToken
    otro = User.objects.create_user(email='otro@ejemplo.com', password=_TEST_PWD)
    _make_tenant(otro)
    client.force_login(otro)

    tok = LoginToken.objects.create(user=magic_user, redirect_to='/dashboard/')
    client.get(f'/auth/entrar/{tok.token}/')

    assert client.session.get('_auth_user_id') == str(magic_user.id)


def test_magic_link_no_lee_next_de_la_url(client, magic_user):
    """Cierra el open redirect: el destino sale SOLO de la fila en BD, así que
    un ?next= inyectado en la URL se ignora por completo."""
    from core.tenant_management.models import LoginToken
    tok = LoginToken.objects.create(user=magic_user, redirect_to='/dashboard/')

    resp = client.get(f'/auth/entrar/{tok.token}/?next=https://sitio-malicioso.com')

    assert resp.status_code == 302
    assert resp.url == '/dashboard/'


def test_magic_link_rate_limit_bloquea_al_intento_11(client, db):
    from django.core.cache import cache
    cache.clear()

    for _ in range(10):
        resp = client.get('/auth/entrar/token-invalido/', HTTP_X_REAL_IP='198.51.100.4')
        assert resp.url.startswith('/auth/login/')

    resp = client.get('/auth/entrar/token-invalido/', HTTP_X_REAL_IP='198.51.100.4')
    assert resp.status_code == 429


def test_magic_link_exitoso_no_cuenta_contra_el_rate_limit(client, magic_user):
    """Un usuario legítimo que abre su link muchas veces nunca debe toparse
    con el límite."""
    from django.core.cache import cache
    from core.tenant_management.models import LoginToken
    cache.clear()
    tok = LoginToken.objects.create(user=magic_user, redirect_to='/dashboard/')

    for _ in range(15):
        resp = client.get(f'/auth/entrar/{tok.token}/', HTTP_X_REAL_IP='198.51.100.9')
        assert resp.status_code == 302
        assert resp.url == '/dashboard/'
```

Verificar que el archivo ya tenga el fixture `_make_tenant` y la constante `_TEST_PWD` (los tiene, líneas 12 y 19). Si `client` no está disponible como fixture de pytest-django, usar `Client()` directamente.

- [ ] **Step 2: Correr los tests para verificar que fallan**

Run: `docker compose exec -T backend python -m pytest core/brand_dna/tests/test_auth_views.py -q -k magic`
Expected: FAIL con 404 (la ruta no existe todavía)

- [ ] **Step 3: Agregar la métrica**

En `core/shared/metrics.py`, después del bloque `EMAIL_VERIFICATIONS` (línea ~67) y antes de `INVITATION_CODES_REDEEMED`:

```python
MAGIC_LOGINS = Counter(
    'cosmic_magic_logins_total',
    'Magic link auto-logins',
    ['result'],
)
```

- [ ] **Step 4: Importar la métrica en `auth_views.py`**

En `core/brand_dna/auth_views.py`, cambiar el bloque de import de líneas 14-17:

```python
from core.shared.metrics import (
    LOGIN_ATTEMPTS, REGISTRATIONS, EMAIL_VERIFICATIONS,
    INVITATION_CODES_REDEEMED, EMAILS_SENT, MAGIC_LOGINS,
)
```

- [ ] **Step 5: Escribir la vista**

Primero los imports. `auth_views.py` hoy importa hasta `from django.utils.html import escape` (línea 11) y **no** tiene `timezone`, `HttpResponse`, `reverse` ni `urlencode`. Agregar los cuatro después de la línea 11:

```python
from django.http import HttpResponse
from django.urls import reverse
from django.utils import timezone
from django.utils.http import urlencode
```

Y la vista, al final del archivo:

```python
_MAGIC_LOGIN_MAX_ATTEMPTS = 10
_MAGIC_LOGIN_WINDOW = 300


def magic_login_view(request, token):
    """Auto-login desde un correo (magic link).

    Es un GET que muta estado (crea sesión), técnicamente no idempotente —
    mismo patrón que verify_email_view, y por la misma razón: un correo solo
    puede enlazar un GET.

    El destino sale de LoginToken.redirect_to (BD), NUNCA de un ?next= en la
    URL: eso es lo que impide usar el dominio como trampolín de phishing.
    """
    from core.tenant_management.models import LoginToken

    ip = _get_client_ip(request)
    cache_key = f'magic_login_attempts:{ip}'

    # El límite se evalúa ANTES de tocar la base de datos — si no, el DoS que
    # queremos prevenir seguiría costando un query por request.
    if (cache.get(cache_key) or 0) >= _MAGIC_LOGIN_MAX_ATTEMPTS:
        MAGIC_LOGINS.labels(result='rate_limited').inc()
        return HttpResponse('Demasiados intentos. Intenta de nuevo en 5 minutos.', status=429)

    def _registrar_fallo():
        # Solo los intentos FALLIDOS incrementan el contador. Un usuario
        # legítimo que abre su link diez veces nunca se topa con el límite.
        try:
            cache.incr(cache_key)
        except ValueError:
            cache.set(cache_key, 1, _MAGIC_LOGIN_WINDOW)

    login_url = reverse('login')
    tok = LoginToken.objects.filter(token=token).select_related('user').first()

    if tok is None:
        _registrar_fallo()
        MAGIC_LOGINS.labels(result='invalid').inc()
        return redirect(login_url)

    if not tok.is_valid():
        _registrar_fallo()
        MAGIC_LOGINS.labels(result='expired').inc()
        return redirect(f'{login_url}?{urlencode({"next": tok.redirect_to})}')

    if not tok.user.is_active:
        _registrar_fallo()
        MAGIC_LOGINS.labels(result='inactive').inc()
        return redirect(login_url)

    # login() cicla la sesión: si había sesión de otro usuario en este
    # navegador queda correctamente reemplazada. Si era del mismo usuario,
    # simplemente rota — inofensivo, sin caso especial.
    login(request, tok.user)

    tok.used_count += 1
    tok.last_used_at = timezone.now()
    tok.last_used_ip = ip or None
    tok.save(update_fields=['used_count', 'last_used_at', 'last_used_ip'])

    MAGIC_LOGINS.labels(result='success').inc()
    # El 302 saca el token de la barra de direcciones, así que no queda
    # visible ni se filtra por el header Referer.
    return redirect(tok.redirect_to)
```

Los cuatro imports del Step 5 cubren todo lo que usa esta vista.

- [ ] **Step 6: Agregar la ruta**

En `core/brand_dna/urls.py`, después de la línea 28 (`auth/reactivate/`):

```python
    path('auth/entrar/<str:token>/', auth_views.magic_login_view, name='magic_login'),
```

- [ ] **Step 7: Correr los tests para verificar que pasan**

Run: `docker compose exec -T backend python -m pytest core/brand_dna/tests/test_auth_views.py -q -k magic`
Expected: PASS, 10 tests

- [ ] **Step 8: Correr la suite del módulo**

Run: `docker compose exec -T backend python -m pytest core/brand_dna/ -q`
Expected: PASS, sin regresiones

- [ ] **Step 9: Commit**

```bash
git add core/shared/metrics.py core/brand_dna/auth_views.py core/brand_dna/urls.py core/brand_dna/tests/test_auth_views.py
GIT_EDITOR=true git commit -m "feat(brand_dna): vista de magic link con rate limit por IP y metrica MAGIC_LOGINS"
```

---

## Task 3: Helper `_magic_url` y los 7 correos

**Files:**
- Modify: `core/content_pipeline/email_sender.py` (helper nuevo tras `_fecha_es` en línea ~24; 7 call sites en líneas 28, 48, 68, 96-98, 146, 193, 212)
- Test: `core/content_pipeline/tests/test_email_sender.py`

**Interfaces:**
- Consumes: `LoginToken` de Task 1; ruta `name='magic_login'` de Task 2.
- Produces: `_magic_url(user, destination_path: str) -> str`, función a nivel de módulo (no método de `EmailSender`).

**Los 7 correos y su camino al user** (verificado contra el código real):

| Método | Línea | Variable | Camino al `user` |
|---|---|---|---|
| `send_initial` | 28 | `calendar_url` | `job.user` |
| `send_month_ready` | 48 | `calendar_url` | `job.user` |
| `send_week_ready` | 68 | `calendar_url` | `job.user` |
| `send_daily` | 96 | `calendar_review_url` | `locked_post.calendar.brand_dna.job.user` |
| `send_payment_failed` | 146 | `dashboard_url` | `job.user` |
| `send_reactivation_calendar` | 193 | `calendar_review_url` | `job.user` (ya resuelto en línea 192) |
| `send_reactivation_analysis` | 212 | `analysis_url` | `user` (parámetro directo) |

`send_trial_expired` (122) y `send_month_expired` (167) **no cambian**: su único link va a Stripe.

- [ ] **Step 1: Escribir los tests que fallan**

Agregar al final de `core/content_pipeline/tests/test_email_sender.py`:

El fixture `full_setup` (línea 12) devuelve la **tupla** `(job, dna, calendar, posts)`, y crea el `AnalysisJob` **sin user**. Los tests existentes del archivo mockean `send_mail` con `patch('core.content_pipeline.email_sender.send_mail')`, así que `mail.outbox` nunca se llena — el HTML se inspecciona desde `mock_send.call_args[1]['html_message']`. Los tests nuevos siguen ese mismo estilo.

```python
# ── Magic link en los correos ──

@pytest.fixture
def job_con_user(full_setup):
    """full_setup crea el AnalysisJob SIN user (el campo es nullable).
    Aquí se le asigna uno para ejercitar el camino con magic link."""
    from django.contrib.auth import get_user_model
    User = get_user_model()
    job, dna, calendar, posts = full_setup
    user = User.objects.create_user(email='dueno@ejemplo.com', password='Str0ng-Pwd-123!')
    job.user = user
    job.save(update_fields=['user'])
    return job, dna, calendar, posts, user


def test_magic_url_crea_token_con_el_destino_correcto(db):
    from django.contrib.auth import get_user_model
    from core.content_pipeline.email_sender import _magic_url
    from core.tenant_management.models import LoginToken
    User = get_user_model()
    user = User.objects.create_user(email='mu@ejemplo.com', password='Str0ng-Pwd-123!')

    url = _magic_url(user, '/calendar/abc/')

    tok = LoginToken.objects.get(user=user)
    assert tok.redirect_to == '/calendar/abc/'
    assert tok.token in url
    assert url.startswith('http')


def test_magic_url_sin_user_devuelve_link_normal(db):
    """AnalysisJob.user es nullable (on_delete=SET_NULL): un job cuyo usuario
    fue eliminado sigue mandando correos. Ese caso es ESPERADO, no excepcional,
    así que se atiende con un guard explícito en vez de dejarlo caer al
    except — si no, cada correo de un job sin usuario escribiría un stack
    trace completo en los logs."""
    from core.content_pipeline.email_sender import _magic_url
    from core.tenant_management.models import LoginToken

    url = _magic_url(None, '/dashboard/')

    assert url.endswith('/dashboard/')
    assert '/auth/entrar/' not in url
    assert LoginToken.objects.count() == 0


def test_magic_url_fail_open_si_falla_la_creacion_del_token(db):
    """LA PRUEBA MÁS IMPORTANTE DEL PLAN: si la base de datos falla al crear el
    token, el correo que anuncia el contenido generado DEBE salir igual, con el
    link de siempre. Nunca un fallo del magic link puede bloquear el correo que
    entrega el valor."""
    from django.contrib.auth import get_user_model
    from core.content_pipeline.email_sender import _magic_url
    User = get_user_model()
    user = User.objects.create_user(email='fo@ejemplo.com', password='Str0ng-Pwd-123!')

    with patch(
        'core.tenant_management.models.LoginToken.objects.create',
        side_effect=Exception('base de datos caida'),
    ):
        url = _magic_url(user, '/calendar/xyz/')

    assert url.endswith('/calendar/xyz/')
    assert '/auth/entrar/' not in url


@override_settings(DEFAULT_FROM_EMAIL='noreply@cosmic.mx')
def test_los_cinco_correos_de_calendario_llevan_magic_link(job_con_user):
    """Los 5 correos que aterrizan en calendar_review. Se prueban juntos porque
    comparten destino: si uno se queda sin _magic_url, este test lo atrapa."""
    from core.content_pipeline.email_sender import EmailSender
    from core.tenant_management.models import LoginToken
    job, dna, calendar, posts, user = job_con_user
    sender = EmailSender()
    destino = f'/calendar/{job.id}/'

    llamadas = [
        lambda: sender.send_initial(job=job, brand_dna=dna),
        lambda: sender.send_month_ready(job=job, brand_dna=dna),
        lambda: sender.send_week_ready(job=job, brand_dna=dna),
        lambda: sender.send_daily(post=posts[0]),
        lambda: sender.send_reactivation_calendar(calendar=calendar),
    ]

    for llamada in llamadas:
        LoginToken.objects.all().delete()
        with patch('core.content_pipeline.email_sender.send_mail') as mock_send:
            llamada()
        tok = LoginToken.objects.get(user=user)
        assert tok.redirect_to == destino
        assert f'/auth/entrar/{tok.token}/' in mock_send.call_args[1]['html_message']


@override_settings(DEFAULT_FROM_EMAIL='noreply@cosmic.mx')
def test_payment_failed_lleva_magic_link_al_dashboard(job_con_user):
    from core.content_pipeline.email_sender import EmailSender
    from core.tenant_management.models import LoginToken
    job, dna, calendar, posts, user = job_con_user

    with patch('core.content_pipeline.email_sender.send_mail') as mock_send:
        EmailSender().send_payment_failed(job=job, brand_dna=dna)

    tok = LoginToken.objects.get(user=user)
    assert tok.redirect_to == '/dashboard/'
    assert f'/auth/entrar/{tok.token}/' in mock_send.call_args[1]['html_message']


@override_settings(DEFAULT_FROM_EMAIL='noreply@cosmic.mx')
def test_reactivation_analysis_lleva_magic_link_a_nuevo_analisis(db):
    from django.contrib.auth import get_user_model
    from core.content_pipeline.email_sender import EmailSender
    from core.tenant_management.models import LoginToken
    User = get_user_model()
    user = User.objects.create_user(email='react@ejemplo.com', password='Str0ng-Pwd-123!')

    with patch('core.content_pipeline.email_sender.send_mail') as mock_send:
        EmailSender().send_reactivation_analysis(user=user)

    tok = LoginToken.objects.get(user=user)
    assert tok.redirect_to.endswith('/nuevo-analisis/') or 'analisis' in tok.redirect_to
    assert f'/auth/entrar/{tok.token}/' in mock_send.call_args[1]['html_message']


@override_settings(DEFAULT_FROM_EMAIL='noreply@cosmic.mx')
def test_los_dos_correos_de_stripe_no_generan_token(job_con_user):
    """Su único link va a Stripe (fuera del dominio) — no hay vista de Django
    que auto-loguear."""
    from core.content_pipeline.email_sender import EmailSender
    from core.tenant_management.models import LoginToken
    job, dna, calendar, posts, user = job_con_user
    sender = EmailSender()

    with patch('core.content_pipeline.email_sender.send_mail'):
        sender.send_trial_expired(job=job, brand_dna=dna)
        sender.send_month_expired(job=job, brand_dna=dna)

    assert LoginToken.objects.count() == 0
```

Nota sobre `send_daily`: consume el post (lo marca `STATUS_SENT`), por eso el bucle usa `posts[0]` una sola vez. Si al correr falla por el `select_for_update` sobre un post ya enviado, usar un post distinto por iteración.

Nota sobre `test_reactivation_analysis`: el assert del `redirect_to` es laxo a propósito porque el path de `new_analysis` se resuelve con `reverse()`. Al implementar, sustituirlo por el valor exacto que devuelva `reverse('new_analysis')`.

- [ ] **Step 2: Correr los tests para verificar que fallan**

Run: `docker compose exec -T backend python -m pytest core/content_pipeline/tests/test_email_sender.py -q -k "magic or correos or stripe_no_generan"`
Expected: FAIL con `ImportError: cannot import name '_magic_url'`

- [ ] **Step 3: Escribir el helper**

En `core/content_pipeline/email_sender.py`, después de `_fecha_es` (línea ~24):

```python
def _magic_url(user, destination_path: str) -> str:
    """URL de auto-login que aterriza en destination_path.

    Dos caminos de degradación distintos, a propósito:
      - user None (AnalysisJob.user es nullable, on_delete=SET_NULL): caso
        ESPERADO, se devuelve el link normal en silencio.
      - excepción al crear el token: fallo genuino, se registra y se devuelve
        el link normal. El correo NUNCA se bloquea por esto: es el mismo
        criterio fail-open de ProductPhotoAnalyzer y el precheck de copyright.
    """
    full_path = settings.COSMIC_BASE_URL + destination_path
    if user is None:
        return full_path
    try:
        from core.tenant_management.models import LoginToken
        tok = LoginToken.objects.create(user=user, redirect_to=destination_path)
        return settings.COSMIC_BASE_URL + reverse('magic_login', args=[tok.token])
    except Exception:
        logger.exception("No se pudo crear LoginToken — se envía link sin auto-login")
        return full_path
```

- [ ] **Step 4: Cambiar los 7 call sites**

`send_initial` (línea 28):
```python
        calendar_url = _magic_url(job.user, reverse('calendar_review', args=[job.id]))
```

`send_month_ready` (línea 48):
```python
        calendar_url = _magic_url(job.user, reverse('calendar_review', args=[job.id]))
```

`send_week_ready` (línea 68):
```python
        calendar_url = _magic_url(job.user, reverse('calendar_review', args=[job.id]))
```

`send_daily` (líneas 96-98):
```python
            calendar_review_url = _magic_url(
                locked_post.calendar.brand_dna.job.user,
                reverse('calendar_review', args=[locked_post.calendar.brand_dna.job.id]),
            )
```

`send_payment_failed` (línea 146):
```python
        dashboard_url = _magic_url(job.user, reverse('dashboard'))
```

`send_reactivation_calendar` (línea 193):
```python
        calendar_review_url = _magic_url(job.user, reverse('calendar_review', args=[job.id]))
```

`send_reactivation_analysis` (línea 212):
```python
        analysis_url = _magic_url(user, reverse('new_analysis'))
```

**No tocar** `send_trial_expired` (122) ni `send_month_expired` (167).
**No tocar** ninguno de los 7 templates HTML: siguen recibiendo las mismas variables de contexto, solo cambia el valor.

- [ ] **Step 5: Correr los tests para verificar que pasan**

Run: `docker compose exec -T backend python -m pytest core/content_pipeline/tests/test_email_sender.py -q`
Expected: PASS, incluidos los tests preexistentes del archivo

- [ ] **Step 6: Recrear los contenedores**

Run: `docker compose up -d --force-recreate --no-deps backend rqworker`

Esto es obligatorio (H93): tras tocar `email_sender.py` no basta con `restart`, y `rqworker` también debe recrearse — si no, los correos que salen desde jobs de RQ siguen ejecutando el código viejo en silencio.

- [ ] **Step 7: Correr la suite del módulo**

Run: `docker compose exec -T backend python -m pytest core/content_pipeline/ -q`
Expected: PASS, sin regresiones

- [ ] **Step 8: Commit**

```bash
git add core/content_pipeline/email_sender.py core/content_pipeline/tests/test_email_sender.py
GIT_EDITOR=true git commit -m "feat(content_pipeline): los 7 correos con vista logueada llevan magic link (fail-open)"
```

---

## Task 4: Comando `purge_login_tokens`

**Files:**
- Create: `core/tenant_management/management/commands/purge_login_tokens.py`
- Test: `core/tenant_management/tests/test_purge_login_tokens_command.py`

**Interfaces:**
- Consumes: `LoginToken` de Task 1 (campo `expires_at`).
- Produces: comando `purge_login_tokens`, dry-run por default, `--apply` para borrar de verdad.

- [ ] **Step 1: Escribir los tests que fallan**

Crear `core/tenant_management/tests/test_purge_login_tokens_command.py`:

```python
import pytest
import secrets
from io import StringIO
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.utils import timezone
from core.tenant_management.models import LoginToken

pytestmark = pytest.mark.django_db

User = get_user_model()
_TEST_PWD = f"T3st-{secrets.token_urlsafe(10)}!"


@pytest.fixture
def user():
    return User.objects.create_user(email='purga@ejemplo.com', password=_TEST_PWD)


def _token_expirado(user):
    return LoginToken.objects.create(
        user=user, redirect_to='/dashboard/',
        expires_at=timezone.now() - timezone.timedelta(hours=1),
    )


def _token_vivo(user):
    return LoginToken.objects.create(
        user=user, redirect_to='/dashboard/',
        expires_at=timezone.now() + timezone.timedelta(hours=1),
    )


def test_dry_run_por_default_no_borra_nada(user):
    """Mismo criterio que migrate_testers_to_founder: sin --apply, el comando
    solo reporta."""
    _token_expirado(user)
    _token_expirado(user)
    _token_vivo(user)

    out = StringIO()
    call_command('purge_login_tokens', stdout=out)

    assert LoginToken.objects.count() == 3
    assert '2' in out.getvalue()


def test_apply_borra_solo_los_expirados(user):
    _token_expirado(user)
    _token_expirado(user)
    vivo = _token_vivo(user)

    call_command('purge_login_tokens', '--apply', stdout=StringIO())

    assert LoginToken.objects.count() == 1
    assert LoginToken.objects.first().id == vivo.id


def test_apply_sin_expirados_no_falla(user):
    _token_vivo(user)

    call_command('purge_login_tokens', '--apply', stdout=StringIO())

    assert LoginToken.objects.count() == 1


def test_es_idempotente(user):
    _token_expirado(user)

    call_command('purge_login_tokens', '--apply', stdout=StringIO())
    call_command('purge_login_tokens', '--apply', stdout=StringIO())

    assert LoginToken.objects.count() == 0
```

- [ ] **Step 2: Correr los tests para verificar que fallan**

Run: `docker compose exec -T backend python -m pytest core/tenant_management/tests/test_purge_login_tokens_command.py -q`
Expected: FAIL con `CommandError: Unknown command: 'purge_login_tokens'`

- [ ] **Step 3: Escribir el comando**

Crear `core/tenant_management/management/commands/purge_login_tokens.py`:

```python
import logging
from django.core.management.base import BaseCommand
from django.utils import timezone
from core.tenant_management.models import LoginToken

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = (
        'Borra los LoginToken (magic links) ya expirados. Dry-run por default '
        '-- requiere --apply para borrar de verdad. Pensado para el mismo cron '
        'externo donde corre send_reactivation_emails. Si nunca se corre no se '
        'rompe nada: la tabla solo acumula filas muertas.'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--apply', action='store_true',
            help='Borra los tokens de verdad. Sin este flag, solo se reporta.',
        )

    def handle(self, *args, **options):
        apply_changes = options['apply']
        expirados = LoginToken.objects.filter(expires_at__lt=timezone.now())
        total = expirados.count()

        if not apply_changes:
            self.stdout.write(
                f'[dry-run] Se borrarian {total} LoginToken expirados. '
                f'Corre con --apply para ejecutar.'
            )
            return

        expirados.delete()
        self.stdout.write(self.style.SUCCESS(f'{total} LoginToken expirados borrados.'))
        logger.info(f'purge_login_tokens: {total} tokens expirados borrados')
```

- [ ] **Step 4: Correr los tests para verificar que pasan**

Run: `docker compose exec -T backend python -m pytest core/tenant_management/tests/test_purge_login_tokens_command.py -q`
Expected: PASS, 4 tests

- [ ] **Step 5: Probar el comando de verdad en dry-run**

Run: `docker compose exec -T backend python manage.py purge_login_tokens`
Expected: imprime `[dry-run] Se borrarian N LoginToken expirados.` sin borrar nada

- [ ] **Step 6: Commit**

```bash
git add core/tenant_management/management/commands/purge_login_tokens.py core/tenant_management/tests/test_purge_login_tokens_command.py
GIT_EDITOR=true git commit -m "feat(tenant_management): comando purge_login_tokens (dry-run por default)"
```

---

## Verificación final

- [ ] **Correr la suite completa**

Run: `docker compose exec -T backend python -m pytest -q`
Expected: 817 tests previos + los nuevos, todos pasando, cero regresiones

- [ ] **Verificar que el ruido preexistente sigue sin commitear**

Run: `git status`
Expected: los archivos listados en Global Constraints siguen sin trackear o sin stage — ninguno debió entrar a un commit de este plan.

- [ ] **Prueba manual en el navegador**

1. Generar un `LoginToken` desde el shell:
   `docker compose exec -T backend python manage.py shell -c "from core.tenant_management.models import LoginToken; from django.contrib.auth import get_user_model; u=get_user_model().objects.get(email='<tu-email>'); print(LoginToken.objects.create(user=u, redirect_to='/dashboard/').token)"`
2. Abrir `https://<dominio-dev>/auth/entrar/<token>/` en una **ventana de incógnito** (sin sesión previa).
3. Confirmar que aterriza en el dashboard ya logueado y que la barra de direcciones muestra `/dashboard/`, no el token.

---

## Pendiente operativo para Anuar (no bloquea el plan)

`purge_login_tokens` necesita engancharse al cron externo donde ya corre `send_reactivation_emails`. Sin eso el sistema funciona igual — la tabla solo acumula filas expiradas a ritmo de ~93/día.
