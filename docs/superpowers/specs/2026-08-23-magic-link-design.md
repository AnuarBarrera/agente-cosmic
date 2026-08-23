# Magic link (auto-login desde correo) — Diseño

**Fecha:** 2026-08-23
**Estado:** aprobado, listo para plan de implementación
**Origen:** hallazgo #10 de `cambiosUI.md` (rediseño de UX para auto-onboarding)

---

## 1. El problema

El usuario llena el formulario de análisis, espera 15-25 minutos mientras la IA
genera su calendario, y recibe el correo de "tu contenido está listo". Al abrirlo
le piden contraseña. Un porcentaje alto se pierde ahí — justo en el momento de
máximo valor entregado.

### Evidencia recogida del código real

No existe hoy ningún mecanismo de auto-login: la búsqueda de
`magic_link|MagicLink|login_token|one_time_token|sesame` sobre todo `core/`
devuelve cero resultados.

La configuración de sesión hace que el problema sea peor de lo que parecía
(`saas_chatbot/settings.py`):

```
SESSION_COOKIE_AGE = 3600            # 1 hora
SESSION_EXPIRE_AT_BROWSER_CLOSE = True
SESSION_INACTIVITY_TIMEOUT = 1800    # 30 min (SessionTimeoutMiddleware)
```

Con una generación de 15-25 minutos:

- **En escritorio**, el usuario que cierra la pestaña y vuelve cuando llega el
  correo tiene alta probabilidad de encontrar la sesión muerta por inactividad.
  No hace falta que sea móvil.
- **En móvil es peor**: el in-app browser de Gmail no comparte cookies con
  Chrome, así que *nunca* hay sesión ahí, sin importar los timeouts.

### Precedente existente

El patrón "token por correo que loguea al usuario" ya está construido y en
producción: `core/brand_dna/auth_views.py:651` (verificación de email) valida un
token recibido por correo y ejecuta `login(request, user)` + `redirect('dashboard')`.

El molde de modelo también existe dos veces en
`core/tenant_management/models.py`: `EmailVerificationToken` (línea 170) y
`PasswordResetToken` (línea 199).

Este diseño no abre una superficie de riesgo nueva: reusa una que ya fue
aceptada y auditada.

---

## 2. Decisiones tomadas

Todas confirmadas explícitamente con Anuar durante el brainstorm.

| Decisión | Elección | Razón |
|---|---|---|
| Alcance de la sesión | **Sesión completa** | Igual que la verificación de email ya en producción. Un token de acceso limitado solo movería la pared de lugar. |
| Vigencia | **72 horas, reutilizable** | Reutilizable evita que el prefetch de Gmail/Outlook queme el token antes del clic, y permite abrirlo en celular y computadora. 72h es la ventana conservadora elegida sobre 7 días. |
| Mitigación de riesgo | **Aceptar y registrar** | Quien tenga el correo entra — mismo modelo que password reset y verificación de email. Se guarda IP y fecha de cada uso para auditoría posterior. |
| Implementación | **Modelo `LoginToken` propio** | Espeja `PasswordResetToken`. Da revocabilidad individual (borrar la fila) y registro de uso, cosas que un token firmado sin estado no ofrece sin una tabla igual. |
| Correos cubiertos | **Los 7 que llevan a vistas con login** | Los 2 restantes van a Stripe, fuera del dominio. |

### Por qué los 2 correos de Stripe quedan fuera

`send_trial_expired` y `send_month_expired` (`content_pipeline/email_sender.py:122,167`,
disparados por `expire_stale_trials_task()` en `tasks.py:592`) tienen un único
link, hacia `get_payment_url(job.user)` → `buy.stripe.com`. No hay vista de
Django que auto-loguear.

Tampoco hace falta cubrir el retorno de Stripe: **el pago siempre se inicia
desde el panel ya logueado** (los dos botones de `calendar_review.html` y el
`early_cta` de `dashboard.html`), y la transacción toma segundos — muy por
debajo del timeout de inactividad de 30 minutos. El usuario vuelve con su sesión
intacta. El correo que llega después con el contenido ya generado
(`send_month_ready`) sí lleva magic link, y está entre los 7.

---

## 3. Modelo de datos

Modelo nuevo en `core/tenant_management/models.py`, junto a sus dos hermanos:

```python
def generate_login_token():
    """Token de auto-login para magic links enviados por correo."""
    return secrets.token_urlsafe(32)


class LoginToken(models.Model):
    """Token de auto-login enviado por correo (magic link)."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    token = models.CharField(max_length=64, unique=True, default=generate_login_token)
    redirect_to = models.CharField(max_length=255)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    used_count = models.IntegerField(default=0)
    last_used_at = models.DateTimeField(null=True, blank=True)
    last_used_ip = models.GenericIPAddressField(null=True, blank=True)

    def save(self, *args, **kwargs):
        if not self.expires_at:
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

**Diferencia deliberada con sus hermanos:** no lleva `is_used`. Este token es
reutilizable dentro de su ventana, así que `used_count` reemplaza el booleano y
`is_valid()` solo verifica expiración.

**Entropía:** `secrets.token_urlsafe(32)` = 256 bits, idéntico a los otros dos
tokens del sistema.

**Volumen esperado:** el correo diario es el que más genera. Con ~93 negocios
activos son ~93 filas/día y ~280 vivas en cualquier momento.

---

## 4. La vista de consumo

**URL:** `path('auth/entrar/<str:token>/', auth_views.magic_login_view, name='magic_login')`

Misma forma que `auth/verify/<str:token>/` y `auth/reset-password/<str:token>/`.
Colgarla bajo `/auth/` la exenta automáticamente del `TenantIsolationMiddleware`,
que ya lista ese prefijo en `PUBLIC_PATH_PREFIXES`
(`core/shared/middleware/tenant_isolation.py:11`). El `SessionTimeoutMiddleware`
no interfiere: solo actúa sobre requests ya autenticadas.

### El destino vive en el token, no en la URL

`redirect_to` se guarda en la fila al crear el token. La alternativa obvia
(`?next=` como query param) abre un **open redirect**: cualquiera podría enviar
`…/auth/entrar/TOKEN/?next=https://sitio-malicioso.com` y usar el dominio de
Cosmic como trampolín de phishing. Guardándolo del lado del servidor, el destino
no es manipulable y no hace falta validarlo contra una allowlist.

### Flujo

La vista responde a **GET** (el clic en el correo). Es un GET que muta estado
—crea sesión—, técnicamente no idempotente; es el mismo patrón que ya usa
`verify_email_view` en producción y se acepta por la misma razón: un correo solo
puede enlazar un GET.

0. **Rate limit primero**, antes de tocar la base de datos (ver sección 6).
1. **Token inexistente** → redirect a `login` con mensaje ("Este enlace ya
   venció, entra con tu correo"). Sin `?next=`.
2. **Token expirado** → mismo redirect, pero con `?next=` apuntando a su
   `redirect_to` (seguro: viene de la BD). Tras poner su contraseña el usuario
   cae donde iba, no en el dashboard genérico.
3. **`user.is_active == False`** → login normal. Una cuenta desactivada no se
   revive por magic link; para eso existe `auth/reactivate/`.
4. **Token válido** → `login(request, user)`. Django cicla la sesión, así que
   una sesión previa de otro usuario en ese navegador queda correctamente
   reemplazada. Si ya había sesión del *mismo* usuario, `login()` simplemente
   rota la sesión: es inofensivo y no requiere caso especial.
5. Registrar uso: `used_count += 1`, `last_used_at`, `last_used_ip`
   (vía `_get_client_ip(request)`, ya existente en `auth_views.py`).
6. `redirect(token.redirect_to)`. El 302 saca el token de la barra de
   direcciones, evitando que quede visible o se filtre por el header `Referer`.

### Comportamientos conocidos, documentados a propósito

- **`used_count` no es una métrica de producto.** El prefetch de Gmail sumará 1
  con la IP de Google antes de que el usuario toque nada. Sirve para auditoría
  forense ("¿desde qué IPs se usó este token?"), no para medir aperturas reales.
  Eso va por GA4.
- **La sesión creada por magic link muere igual que cualquier otra**: 30 min de
  inactividad, 1 hora de vida, o al cerrar el navegador. El magic link resuelve
  la puerta de entrada, no la permanencia.

---

## 5. Integración con `email_sender.py`

Helper privado nuevo:

```python
def _magic_url(user, destination_path: str) -> str:
    """URL de auto-login que aterriza en destination_path.
    Fail-open: si el token no se puede crear, devuelve el link normal."""
    try:
        tok = LoginToken.objects.create(user=user, redirect_to=destination_path)
        return settings.COSMIC_BASE_URL + reverse('magic_login', args=[tok.token])
    except Exception:
        logger.exception("No se pudo crear LoginToken — se envía link sin auto-login")
        return settings.COSMIC_BASE_URL + destination_path
```

Cada método cambia una línea:

```python
# antes
calendar_url = settings.COSMIC_BASE_URL + reverse('calendar_review', args=[job.id])
# después
calendar_url = _magic_url(job.user, reverse('calendar_review', args=[job.id]))
```

### Los 7 correos y su camino al `user`

| Método | Destino | Camino al `user` |
|---|---|---|
| `send_initial` | `calendar_review` | `job.user` |
| `send_month_ready` | `calendar_review` | `job.user` |
| `send_week_ready` | `calendar_review` | `job.user` |
| `send_daily` | `calendar_review` | `post.calendar.brand_dna.job.user` |
| `send_reactivation_calendar` | `calendar_review` | `calendar.brand_dna.job.user` |
| `send_payment_failed` | `dashboard` | `job.user` |
| `send_reactivation_analysis` | `new_analysis` | `user` (parámetro directo) |

**Los 7 templates HTML no se tocan.** Siguen recibiendo `calendar_url` /
`dashboard_url` / `analysis_url` con el mismo nombre de variable; solo cambia el
valor. Cero riesgo de romper el diseño de los correos.

### El fail-open es deliberado

Si la base de datos falla justo al crear el token, el correo **igual sale**, con
el link de siempre. Nunca debe un fallo del magic link bloquear el correo que
anuncia el valor entregado. Es el mismo criterio que ya usan
`ProductPhotoAnalyzer` y el precheck de copyright en este repo.

### Caso conocido: `job.email` ≠ `job.user.email`

`send_initial` envía a `job.email`, que no necesariamente coincide con
`job.user.email`. El token se ata a `job.user`. Si esas direcciones difieren,
quien reciba ese correo entra como `job.user` — el mismo riesgo ya aceptado
("quien tenga el correo, entra"), pero aquí puede darse sin reenvío de por
medio. Se documenta; no se bloquea.

---

## 6. Seguridad, observabilidad y mantenimiento

### Rate limit

No es contra fuerza bruta (adivinar 256 bits no es un escenario real), sino
contra **DoS barato**: sin límite, cualquiera puede martillar
`/auth/entrar/<basura>/` y cada request cuesta un query.

Se reusa el patrón exacto de `login_view` (`auth_views.py:245-265`):
`cache.incr` sobre `magic_login_attempts:{ip}`, **10 intentos fallidos por IP en
5 minutos**.

**Mecánica exacta** (para que no quede a interpretación): al entrar la request se
lee el contador; si ya supera 10, se corta con `result='rate_limited'` sin tocar
la base de datos. Si no, se busca el token. **El contador se incrementa
únicamente cuando el token resulta inválido, inexistente o expirado** — nunca en
un acceso exitoso. Así un usuario legítimo que abre su link diez veces desde la
misma IP jamás se topa con el límite, mientras que quien prueba tokens al azar
se agota a los 10 intentos.

### Observabilidad

Métrica nueva `MAGIC_LOGINS` en `core/shared/metrics.py`, siguiendo la forma de
`LOGIN_ATTEMPTS` (línea 51), con label `result`:
`success` / `expired` / `invalid` / `inactive` / `rate_limited`.

Esta métrica es la que permite validar en producción la hipótesis de
auto-onboarding: dice si la gente está entrando por magic link o cayendo al
login de todas formas.

### Purga

Management command `purge_login_tokens` que borra los expirados, con `--dry-run`
por default (mismo patrón que `migrate_testers_to_founder`). Se engancha al cron
externo donde ya corre `send_reactivation_emails`.

Si nunca se corre, no se rompe nada: solo acumula filas muertas a ritmo de
~93/día.

---

## 7. Pruebas

TDD, como el resto del repo. Ejecución:
`docker compose exec -T backend python -m pytest <path> -q`

**Modelo:**
- `expires_at` se fija a +72h al guardar.
- `is_valid()` en los bordes (justo antes y justo después de expirar).
- Unicidad del campo `token`.

**Vista:**
- Token válido loguea y redirige a `redirect_to`.
- Token expirado → login con `?next=` al `redirect_to`.
- Token inexistente → login sin `?next=`.
- Usuario inactivo no entra.
- Sesión previa de otro usuario queda reemplazada.
- `used_count`, `last_used_at` y `last_used_ip` se registran.
- Token reutilizado sigue funcionando dentro de la ventana.

**Rate limit:**
- El intento fallido 11 desde una IP se bloquea.
- Los intentos exitosos no cuentan contra el límite.

**`email_sender`:**
- Los 7 correos generan token con el `redirect_to` correcto.
- **El fail-open envía el correo con link normal si `LoginToken.objects.create`
  lanza excepción.** (La prueba más importante del conjunto.)

**Seguridad:**
- `redirect_to` no es manipulable desde la URL — la vista no lee ningún `?next=`.

---

## 8. Fuera de alcance

Explícito, para que no se confunda con un descuido:

- **La sesión sigue muriendo a los 30 minutos de inactividad.** Que el usuario
  que vuelve dos horas después siga dentro requiere cambiar
  `SESSION_INACTIVITY_TIMEOUT`, decisión aparte con su propio costo de seguridad.
- **Los 2 correos que van a Stripe no cambian** (ver sección 2).
- **El usuario que abre el correo al cuarto día cae al login normal.** Es la
  consecuencia directa de elegir 72h sobre 7 días, y es aceptable porque no
  empeora nada respecto del comportamiento actual.
- **Los demás hallazgos de `cambiosUI.md`** (paquete de fricción del registro y
  formulario, rediseño del flujo post-registro, jerarquía ADN/calendario) tienen
  su propio ciclo de diseño.
