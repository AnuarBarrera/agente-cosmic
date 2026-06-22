# Roles, Onboarding y Notificaciones — Design Spec

**Goal:** Implementar sistema de roles (admin/tester/user), admin panel con 2FA via Django Admin, códigos de invitación para testers, verificación de email en registro, y notificaciones al admin por nuevo usuario.

**Architecture:** Django Groups para roles, `django-otp` + TOTP para 2FA en Django Admin, modelo `InvitationCode` para códigos, `EmailVerificationToken` existente para magic links, Mailgun para notificaciones.

**Tech Stack:** Django 5.2, django-otp, qrcode, Mailgun (ya configurado), Django Admin.

---

## 1. Roles y permisos

### Mecanismo: Django Groups

Tres grupos creados via migration:

| Grupo | Plan asignado | `is_staff` | Acceso a `/admin/` |
|-------|--------------|------------|---------------------|
| `admin` | Admin (ilimitado) | `True` | Si, con 2FA |
| `tester` | Tester (nuevo) | `False` | No |
| `user` | Free | `False` | No |

### Plan "Tester" (nuevo)

Se crea en tabla `plans` via data migration:

| Campo | Valor |
|-------|-------|
| `name` | `Tester` |
| `max_calendars_per_week` | 5 |
| `max_post_regenerations` | 5 |
| `max_post_edits` | 5 |
| `price` | 0.00 |

### Asignación de plan por rol

Modificar `get_user_plan()` en `core/brand_dna/rate_limits.py`:

1. Si el usuario tiene `tenant` con `subscription` → usar ese plan (actual)
2. Si no, derivar el plan del grupo del usuario:
   - Grupo `admin` → Plan "Admin"
   - Grupo `tester` → Plan "Tester"
   - Grupo `user` o sin grupo → Plan "Free"

### Asignación de grupo al registro

- Registro sin código de invitación → grupo `user`
- Registro con código de invitación válido → grupo indicado por `InvitationCode.target_group` (default: `tester`)
- Google OAuth → grupo `user` (puede hacer upgrade después desde dashboard)

---

## 2. Admin Panel — Django Admin con 2FA

### Paquetes nuevos

- `django-otp` — framework TOTP/HOTP para Django
- `django-otp[qrcode]` o `qrcode` — generación de QR para setup de TOTP

### Protección de `/admin/`

- Solo usuarios con `is_staff=True` + grupo `admin` pueden acceder
- Login requiere email + contraseña + código TOTP de 6 dígitos
- Primer acceso como admin: se muestra QR code para configurar en Google Authenticator/Authy
- Sin TOTP configurado → se fuerza setup en primer acceso (custom `AdminSite.login` que redirige a setup si no hay device)
- Acceso a `/admin/` por usuario sin `is_staff` → devuelve 404 (no 403)

### Configuración de django-otp

En `settings.py`:
- Agregar `django_otp` y `django_otp.plugins.otp_totp` a `INSTALLED_APPS`
- Agregar `django_otp.middleware.OTPMiddleware` a `MIDDLEWARE` (después de `AuthenticationMiddleware`)
- Reemplazar `admin.site` con `OTPAdminSite` en `urls.py`

### ModelAdmin registrados

| Modelo | Vista | Acciones | Filtros |
|--------|-------|----------|---------|
| `User` | email, grupos, fecha registro, `is_active`, calendarios creados (annotated) | Cambiar grupo, activar/desactivar | grupo, activo, fecha |
| `InvitationCode` | code, target_group, max_uses, times_used, is_active, expires_at, created_by | Crear, desactivar | activo, grupo destino |
| `Plan` | name, todos los límites | Editar límites | - |
| `AnalysisJob` | usuario, business_url, status, stage, created_at | Solo lectura | status, fecha |
| `SecurityEvent` | event_type, user, ip_address, severity, created_at | Solo lectura | tipo, severidad, fecha |

### Acción bulk en User: "Generar código de invitación para seleccionados"

Seleccionar usuarios → acción → genera un `InvitationCode` por cada uno y muestra los códigos generados. Esto es para poder upgrade manual de usuarios existentes a tester.

---

## 3. Códigos de invitación

### Modelo `InvitationCode`

Ubicación: `core/tenant_management/models.py`

| Campo | Tipo | Default | Descripción |
|-------|------|---------|-------------|
| `id` | UUIDField(pk) | uuid4 | - |
| `code` | CharField(12, unique) | auto-generado | Formato `COSMIC-XXXXXX` |
| `target_group` | CharField(20) | `tester` | Grupo que se asigna al usar el código |
| `max_uses` | PositiveIntegerField | 1 | 0 = ilimitado |
| `times_used` | PositiveIntegerField | 0 | Contador |
| `created_by` | FK → User | - | Admin que lo creó |
| `is_active` | BooleanField | True | Desactivación manual |
| `expires_at` | DateTimeField(null) | None | Expiración opcional |
| `created_at` | DateTimeField | auto_now_add | - |

### Generación del código

Formato: `COSMIC-` + 6 caracteres alfanuméricos uppercase (sin ambiguos: sin 0/O/I/1/L).
Generado con `secrets.choice()`.

### Validación

Un código es válido si:
- `is_active == True`
- `expires_at` es None o `expires_at > now()`
- `max_uses == 0` o `times_used < max_uses`

### Método `InvitationCode.redeem(user)`

1. Valida el código
2. Asigna al usuario el grupo indicado por `target_group`
3. Incrementa `times_used`
4. Retorna True/False

---

## 4. Onboarding con verificación de email

### Flujo A: Registro con email/password

1. `GET /auth/register/` → formulario con campos: email, password, confirmar password, código de invitación (opcional)
2. `POST /auth/register/`:
   - Valida email único, `validate_password`, passwords coinciden
   - Valida código de invitación si se proporcionó (si inválido: registro normal como `user`, no bloquea)
   - Crea `EmailVerificationToken` con `user_data`: `{password: make_password(pw), invitation_code: "COSMIC-..." or ""}`
   - Envía email con magic link via Mailgun
   - Redirige a página "Revisa tu correo"
3. `GET /auth/verify/{token}/`:
   - Valida token (no expirado, no usado)
   - Crea usuario con password hasheado del token
   - Asigna grupo/plan según `invitation_code` en `user_data`
   - Marca token como usado
   - Envía notificación al admin
   - Redirige a login con mensaje "Email verificado, ya puedes iniciar sesión"

### Flujo B: Registro con Google OAuth

1. Clic en "Registrarse con Google" → flujo OAuth existente (PKCE + state)
2. `google_callback_view` crea usuario como grupo `user` + plan Free (email ya verificado por Google)
3. Se envía notificación al admin
4. En el dashboard: banner "¿Tienes un código de invitación?" con campo + botón "Aplicar"

### Vista `POST /dashboard/apply-code/`

1. Login required
2. Recibe `code` en POST
3. Valida código con `InvitationCode.redeem(user)`
4. Si válido: upgrade a tester, mensaje de éxito
5. Si inválido: mensaje de error
6. Redirige a dashboard

### Template de registro actualizado

- Campo de código de invitación con estilo consistente (opcional, badge "opcional")
- Botón de Google OAuth se mantiene en la misma posición
- Texto explicativo: "Si tienes un código de invitación, ingrésalo para obtener acceso ampliado"

---

## 5. Notificaciones al admin

### Trigger

Se envía email a `settings.ADMIN_NOTIFICATION_EMAIL` (default: `contacto.neia@gmail.com`) cuando:
- Un usuario nuevo completa verificación de email (Flujo A)
- Un usuario nuevo se registra via Google OAuth (Flujo B)

### Contenido del email

- **Asunto:** `[Agente Cosmic] Nuevo usuario verificado — {email}`
- **Cuerpo HTML:** email del usuario, rol asignado (user/tester), código de invitación usado (si aplica), fecha/hora, link directo al usuario en Django Admin

### Implementación

Función `notify_admin_new_user(user, invitation_code=None)` en `core/brand_dna/auth_views.py` (o un módulo `notifications.py` si se prefiere separar). Usa `django.core.mail.send_mail` con Mailgun (ya configurado).

Se llama desde:
- `verify_email_view` (Flujo A) — después de crear el usuario
- `google_callback_view` (Flujo B) — solo si `created == True`

### Configuración

Nuevo setting: `ADMIN_NOTIFICATION_EMAIL = os.getenv('ADMIN_NOTIFICATION_EMAIL', 'contacto.neia@gmail.com')`

---

## 6. Data migrations

Una migration que crea:
1. Grupos `admin`, `tester`, `user`
2. Plan `Tester` con límites definidos
3. Asigna al superusuario existente (`contacto.neia@gmail.com`) al grupo `admin`
4. Asigna usuarios existentes sin grupo al grupo `user`

---

## 7. Lo que NO se toca

- Flujo de login existente (email + password) — sin cambios
- Dashboard del usuario — solo se agrega banner de código de invitación
- Pipeline de contenido (brand_dna, content_pipeline) — sin cambios
- Middlewares diferidos (session_timeout, tenant_isolation, rate_limiting) — fase 2
- API REST de tenant_management (DRF endpoints) — fase 2
- `MAX_REGISTERED_USERS=30` — se mantiene
