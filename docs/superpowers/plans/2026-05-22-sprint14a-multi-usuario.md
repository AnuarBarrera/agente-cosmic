# Sprint 14A — Multi-usuario Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Añadir soporte multi-usuario al agente: múltiples `chat_id` con niveles de permiso `admin` y `viewer`, de modo que admin tenga acceso total y viewer solo a comandos de lectura/contenido.

**Architecture:** Se agrega campo `role` ('admin'/'viewer') a `AgentSession` (modelo + entidad de dominio). La lista de admins viene de `TELEGRAM_ADMIN_CHAT_IDS` en `.env`. La memoria ya está segmentada por sesión (FK existente en `AgentMemory`), por lo que no requiere cambios adicionales. Nuevos admin-guards protegen comandos operacionales.

**Tech Stack:** Django 5.2, PostgreSQL 16, python-telegram-bot, pytest, Docker Compose.

---

## File Structure

- **Modify**: `core/agent/domain/entities.py` — añadir `role: str = 'viewer'` a `AgentSession`
- **Modify**: `core/agent/infrastructure/models.py` — añadir `role` CharField + `ROLE_CHOICES` a `AgentSession`
- **Create**: `core/agent/migrations/0007_agentsession_role.py`
- **Modify**: `saas_chatbot/settings.py` — añadir `TELEGRAM_ADMIN_CHAT_IDS`
- **Modify**: `core/agent/infrastructure/repositories.py` — sincronizar `role` desde settings en `get_or_create`
- **Modify**: `core/agent/management/commands/run_telegram_bot.py` — `_require_admin` helper, `/usuarios`, admin guards en `/prospectar` y `/contactado`
- **Create**: `core/agent/tests/test_sprint14_multiusuario.py`

---

### Task 1: Campo `role` en modelo y entidad de dominio

**Files:**
- Modify: `core/agent/domain/entities.py`
- Modify: `core/agent/infrastructure/models.py`
- Create: `core/agent/migrations/0007_agentsession_role.py`
- Test: `core/agent/tests/test_sprint14_multiusuario.py`

- [ ] **Step 1: Escribir tests fallando**

Crear `core/agent/tests/test_sprint14_multiusuario.py`:

```python
"""Tests Sprint 14A — Multi-usuario: roles, guards, /usuarios."""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from django.test import override_settings

pytestmark = pytest.mark.django_db


class TestAgentSessionRole:
    def test_default_role_is_viewer(self):
        from core.agent.infrastructure.models import AgentSession
        s = AgentSession.objects.create(chat_id=11100, is_authorized=True)
        assert s.role == 'viewer'

    def test_role_choices_include_admin_and_viewer(self):
        from core.agent.infrastructure.models import AgentSession
        choices = dict(AgentSession.ROLE_CHOICES)
        assert 'admin' in choices
        assert 'viewer' in choices

    def test_admin_role_can_be_set(self):
        from core.agent.infrastructure.models import AgentSession
        s = AgentSession.objects.create(chat_id=11101, is_authorized=True, role='admin')
        s.refresh_from_db()
        assert s.role == 'admin'
```

- [ ] **Step 2: Ejecutar test para verificar que falla**

```bash
docker exec chatbot-backend-1 python -m pytest core/agent/tests/test_sprint14_multiusuario.py::TestAgentSessionRole -v
```
Expected: FAIL — `AttributeError: type object 'AgentSession' has no attribute 'ROLE_CHOICES'`

- [ ] **Step 3: Añadir `role` a la entidad de dominio**

En `core/agent/domain/entities.py`, modificar el dataclass `AgentSession`:

```python
@dataclass
class AgentSession:
    chat_id: int
    username: str
    full_name: str
    is_authorized: bool
    id: Optional[int] = None
    created_at: Optional[datetime] = None
    last_active_at: Optional[datetime] = None
    role: str = 'viewer'
```

- [ ] **Step 4: Añadir `role` al modelo Django**

En `core/agent/infrastructure/models.py`, dentro de `class AgentSession(models.Model)`, añadir después del campo `is_authorized`:

```python
    ROLE_ADMIN = 'admin'
    ROLE_VIEWER = 'viewer'
    ROLE_CHOICES = [('admin', 'Admin'), ('viewer', 'Viewer')]
    role = models.CharField(max_length=20, choices=ROLE_CHOICES, default='viewer')
```

El modelo completo quedará:
```python
class AgentSession(models.Model):
    chat_id = models.BigIntegerField(unique=True)
    username = models.CharField(max_length=255, blank=True, default='')
    full_name = models.CharField(max_length=255, blank=True, default='')
    is_authorized = models.BooleanField(default=False)
    ROLE_ADMIN = 'admin'
    ROLE_VIEWER = 'viewer'
    ROLE_CHOICES = [('admin', 'Admin'), ('viewer', 'Viewer')]
    role = models.CharField(max_length=20, choices=ROLE_CHOICES, default='viewer')
    created_at = models.DateTimeField(auto_now_add=True)
    last_active_at = models.DateTimeField(auto_now=True)
    ...
```

- [ ] **Step 5: Generar migración**

```bash
docker exec chatbot-backend-1 python manage.py makemigrations agent --name agentsession_role
```
Expected: `Migrations for 'agent': core/agent/migrations/0007_agentsession_role.py`

- [ ] **Step 6: Aplicar migración**

```bash
docker exec chatbot-backend-1 python manage.py migrate
```
Expected: `Applying agent.0007_agentsession_role... OK`

- [ ] **Step 7: Verificar que los tests pasan**

```bash
docker exec chatbot-backend-1 python -m pytest core/agent/tests/test_sprint14_multiusuario.py::TestAgentSessionRole -v
```
Expected: PASS (3 tests)

- [ ] **Step 8: Commit**

```bash
git add core/agent/domain/entities.py core/agent/infrastructure/models.py core/agent/migrations/0007_agentsession_role.py core/agent/tests/test_sprint14_multiusuario.py
git commit -m "feat: add role field (admin/viewer) to AgentSession model and domain entity"
```

---

### Task 2: Settings + Repository — sincronizar roles

**Files:**
- Modify: `saas_chatbot/settings.py`
- Modify: `core/agent/infrastructure/repositories.py`
- Test: `core/agent/tests/test_sprint14_multiusuario.py`

- [ ] **Step 1: Escribir tests fallando**

Añadir a `core/agent/tests/test_sprint14_multiusuario.py`:

```python
class TestRoleAssignment:
    def test_admin_chat_id_gets_admin_role(self):
        from core.agent.infrastructure.repositories import DjangoSessionRepository
        with override_settings(
            TELEGRAM_ADMIN_CHAT_IDS=[11200],
            TELEGRAM_AUTHORIZED_CHAT_IDS=[11200, 11201],
        ):
            repo = DjangoSessionRepository()
            session = repo.get_or_create(11200, 'admin_user', 'Admin User')
        assert session.role == 'admin'
        assert session.is_authorized is True

    def test_authorized_non_admin_gets_viewer_role(self):
        from core.agent.infrastructure.repositories import DjangoSessionRepository
        with override_settings(
            TELEGRAM_ADMIN_CHAT_IDS=[11200],
            TELEGRAM_AUTHORIZED_CHAT_IDS=[11200, 11201],
        ):
            repo = DjangoSessionRepository()
            session = repo.get_or_create(11201, 'viewer_user', 'Viewer User')
        assert session.role == 'viewer'
        assert session.is_authorized is True

    def test_unauthorized_user_is_not_authorized(self):
        from core.agent.infrastructure.repositories import DjangoSessionRepository
        with override_settings(
            TELEGRAM_ADMIN_CHAT_IDS=[11200],
            TELEGRAM_AUTHORIZED_CHAT_IDS=[11200, 11201],
        ):
            repo = DjangoSessionRepository()
            session = repo.get_or_create(99999, 'unknown', 'Unknown')
        assert session.is_authorized is False

    def test_role_updates_when_promoted_to_admin(self):
        """Si un chat_id viewer es promovido a admin en .env, el siguiente get_or_create actualiza el rol."""
        from core.agent.infrastructure.repositories import DjangoSessionRepository
        # Primera vez: viewer
        with override_settings(
            TELEGRAM_ADMIN_CHAT_IDS=[],
            TELEGRAM_AUTHORIZED_CHAT_IDS=[11202],
        ):
            repo = DjangoSessionRepository()
            repo.get_or_create(11202, 'user', 'User')
        # Segunda vez: promovido a admin
        with override_settings(
            TELEGRAM_ADMIN_CHAT_IDS=[11202],
            TELEGRAM_AUTHORIZED_CHAT_IDS=[11202],
        ):
            repo = DjangoSessionRepository()
            session = repo.get_or_create(11202, 'user', 'User')
        assert session.role == 'admin'
```

- [ ] **Step 2: Ejecutar tests para verificar que fallan**

```bash
docker exec chatbot-backend-1 python -m pytest core/agent/tests/test_sprint14_multiusuario.py::TestRoleAssignment -v
```
Expected: FAIL — `assert 'viewer' == 'admin'` (el repositorio no asigna rol todavía)

- [ ] **Step 3: Añadir `TELEGRAM_ADMIN_CHAT_IDS` a settings**

En `saas_chatbot/settings.py`, después de `TELEGRAM_AUTHORIZED_CHAT_IDS`:

```python
TELEGRAM_ADMIN_CHAT_IDS = [
    int(cid.strip())
    for cid in get_env('TELEGRAM_ADMIN_CHAT_IDS', '').split(',')
    if cid.strip().isdigit()
]
```

- [ ] **Step 4: Actualizar `DjangoSessionRepository.get_or_create` en repositories.py**

Reemplazar el método completo `get_or_create` en `core/agent/infrastructure/repositories.py`:

```python
def get_or_create(self, chat_id: int, username: str, full_name: str) -> AgentSession:
    admin_ids = getattr(settings, 'TELEGRAM_ADMIN_CHAT_IDS', [])
    authorized_ids = getattr(settings, 'TELEGRAM_AUTHORIZED_CHAT_IDS', [])
    all_authorized = set(admin_ids) | set(authorized_ids)
    expected_role = 'admin' if chat_id in admin_ids else 'viewer'
    expected_auth = chat_id in all_authorized

    obj, created = models.AgentSession.objects.get_or_create(
        chat_id=chat_id,
        defaults={
            'username': username or '',
            'full_name': full_name or '',
            'is_authorized': expected_auth,
            'role': expected_role,
        }
    )
    if not created:
        update_fields = []
        if obj.is_authorized != expected_auth:
            obj.is_authorized = expected_auth
            update_fields.append('is_authorized')
        if obj.role != expected_role:
            obj.role = expected_role
            update_fields.append('role')
        if username and obj.username != username:
            obj.username = username
            update_fields.append('username')
        if full_name and obj.full_name != full_name:
            obj.full_name = full_name
            update_fields.append('full_name')
        if update_fields:
            obj.save(update_fields=update_fields)

    return AgentSession(
        id=obj.id,
        chat_id=obj.chat_id,
        username=obj.username,
        full_name=obj.full_name,
        is_authorized=obj.is_authorized,
        created_at=obj.created_at,
        last_active_at=obj.last_active_at,
        role=obj.role,
    )
```

- [ ] **Step 5: Verificar que los tests pasan**

```bash
docker exec chatbot-backend-1 python -m pytest core/agent/tests/test_sprint14_multiusuario.py::TestRoleAssignment -v
```
Expected: PASS (4 tests)

- [ ] **Step 6: Ejecutar suite completa para verificar sin regresiones**

```bash
docker exec chatbot-backend-1 python -m pytest core/agent/tests/ -q
```
Expected: todos los tests pasan (247+)

- [ ] **Step 7: Commit**

```bash
git add saas_chatbot/settings.py core/agent/infrastructure/repositories.py core/agent/tests/test_sprint14_multiusuario.py
git commit -m "feat: add TELEGRAM_ADMIN_CHAT_IDS, sync role per chat_id in repository"
```

---

### Task 3: Admin guards + comando `/usuarios`

**Files:**
- Modify: `core/agent/management/commands/run_telegram_bot.py`
- Test: `core/agent/tests/test_sprint14_multiusuario.py`

- [ ] **Step 1: Escribir tests fallando**

Añadir a `core/agent/tests/test_sprint14_multiusuario.py`:

```python
class TestAdminGuard:
    @pytest.mark.asyncio
    async def test_viewer_blocked_from_cmd_usuarios(self):
        from core.agent.management.commands.run_telegram_bot import cmd_usuarios
        update = MagicMock()
        update.message.reply_text = AsyncMock()
        update.effective_user.username = 'viewer'
        update.effective_user.full_name = 'Viewer'
        update.effective_chat.id = 300
        context = MagicMock()

        viewer_session = MagicMock(is_authorized=True, role='viewer')
        with patch('core.agent.management.commands.run_telegram_bot.get_or_create_session',
                   return_value=viewer_session):
            await cmd_usuarios(update, context)

        call_text = update.message.reply_text.call_args[0][0]
        assert 'admin' in call_text.lower() or 'administrador' in call_text.lower()

    @pytest.mark.asyncio
    async def test_unauthorized_blocked_from_cmd_usuarios(self):
        from core.agent.management.commands.run_telegram_bot import cmd_usuarios
        update = MagicMock()
        update.message.reply_text = AsyncMock()
        update.effective_user.username = 'unknown'
        update.effective_user.full_name = 'Unknown'
        update.effective_chat.id = 301
        context = MagicMock()

        unauth_session = MagicMock(is_authorized=False, role='viewer')
        with patch('core.agent.management.commands.run_telegram_bot.get_or_create_session',
                   return_value=unauth_session):
            await cmd_usuarios(update, context)

        call_text = update.message.reply_text.call_args[0][0]
        assert 'autorizado' in call_text.lower()

    @pytest.mark.asyncio
    async def test_admin_can_run_cmd_usuarios(self):
        from core.agent.management.commands.run_telegram_bot import cmd_usuarios
        update = MagicMock()
        update.message.reply_text = AsyncMock()
        update.effective_user.username = 'admin'
        update.effective_user.full_name = 'Admin'
        update.effective_chat.id = 302
        context = MagicMock()

        admin_session = MagicMock(is_authorized=True, role='admin')
        with patch('core.agent.management.commands.run_telegram_bot.get_or_create_session',
                   return_value=admin_session), \
             patch('core.agent.management.commands.run_telegram_bot._list_sessions',
                   return_value='📋 *Usuarios autorizados:*\n👑 Admin'):
            await cmd_usuarios(update, context)

        # Debe haber respondido con la lista (no con error de permisos)
        calls = [str(c) for c in update.message.reply_text.call_args_list]
        assert not any('admin' in t.lower() and 'solo' in t.lower() for t in calls)
```

- [ ] **Step 2: Ejecutar tests para verificar que fallan**

```bash
docker exec chatbot-backend-1 python -m pytest core/agent/tests/test_sprint14_multiusuario.py::TestAdminGuard -v
```
Expected: FAIL — `ImportError: cannot import name 'cmd_usuarios'`

- [ ] **Step 3: Añadir `_require_admin`, `_list_sessions_sync`, `cmd_usuarios` al bot**

En `core/agent/management/commands/run_telegram_bot.py`, añadir después de la función `get_consumo = sync_to_async(...)` (línea ~157):

```python
# ─── multi-usuario ──────────────────────────────────────────────────────────

ADMIN_ONLY_MSG = "⛔ Este comando es solo para administradores."


async def _require_admin(update: Update, session) -> bool:
    """Retorna True si el usuario es admin. Envía mensaje de error y retorna False si no lo es."""
    if not session.is_authorized:
        await update.message.reply_text("No estás autorizado para usar este agente.")
        return False
    if getattr(session, 'role', 'viewer') != 'admin':
        await update.message.reply_text(ADMIN_ONLY_MSG)
        return False
    return True


def _list_sessions_sync() -> str:
    from core.agent.infrastructure.models import AgentSession as SessionModel
    sessions = SessionModel.objects.filter(is_authorized=True).order_by('-last_active_at')
    if not sessions.exists():
        return '📋 No hay usuarios autorizados.'
    lines = ['📋 *Usuarios autorizados:*\n']
    for s in sessions:
        role_icon = '👑' if s.role == 'admin' else '👤'
        last = s.last_active_at.strftime('%d/%m %H:%M') if s.last_active_at else '—'
        name = s.full_name or s.username or str(s.chat_id)
        lines.append(f'{role_icon} *{name}* (`{s.role}`) — Último acceso: {last}')
    return '\n'.join(lines)


_list_sessions = sync_to_async(_list_sessions_sync)


async def cmd_usuarios(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/usuarios (solo admin) — Lista todos los usuarios autorizados y sus roles."""
    user = update.effective_user
    session = await get_or_create_session(
        update.effective_chat.id, user.username or '', user.full_name or ''
    )
    if not await _require_admin(update, session):
        return
    text = await _list_sessions()
    await safe_reply(update.message, text)
```

- [ ] **Step 4: Aplicar admin guard a `/prospectar` y `/contactado`**

En `cmd_prospectar`, reemplazar el bloque de autorización:
```python
    # REEMPLAZAR:
    if not session.is_authorized:
        await update.message.reply_text("No estás autorizado para usar este agente.")
        return
    # CON:
    if not await _require_admin(update, session):
        return
```

En `cmd_contactado`, reemplazar:
```python
    # REEMPLAZAR:
    if not session.is_authorized:
        await update.message.reply_text('No estás autorizado para usar este agente.')
        return
    # CON:
    if not await _require_admin(update, session):
        return
```

- [ ] **Step 5: Registrar `/usuarios` en el handler de comandos**

En `Command.handle`, dentro del `Application.add_handler` block, añadir:
```python
        app.add_handler(CommandHandler('usuarios', cmd_usuarios))
```

- [ ] **Step 6: Añadir `/usuarios` al texto de ayuda**

En `AYUDA_TEXT`, añadir al final (antes del cierre de la cadena):
```python
    "👥 */usuarios* _(solo admin)_\n"
    "Lista los usuarios autorizados con su rol y último acceso.\n\n"
```

- [ ] **Step 7: Verificar que los tests pasan**

```bash
docker exec chatbot-backend-1 python -m pytest core/agent/tests/test_sprint14_multiusuario.py::TestAdminGuard -v
```
Expected: PASS (3 tests)

- [ ] **Step 8: Ejecutar suite completa para verificar sin regresiones**

```bash
docker exec chatbot-backend-1 python -m pytest core/agent/tests/ -q
```
Expected: todos los tests pasan

- [ ] **Step 9: Reiniciar bot para cargar cambios**

```bash
docker compose restart telegram_bot
```

- [ ] **Step 10: Añadir `TELEGRAM_ADMIN_CHAT_IDS` al .env**

Añadir al archivo `.env` (o `.env.prod`):
```
TELEGRAM_ADMIN_CHAT_IDS=<tu_chat_id>
```
Para obtener tu chat_id: /start en el bot y el primer número en `/consumo` → `Sesiones autorizadas`.

Recargar containers para que tome el nuevo env var:
```bash
docker compose up -d
```

- [ ] **Step 11: Commit final**

```bash
git add core/agent/management/commands/run_telegram_bot.py core/agent/tests/test_sprint14_multiusuario.py
git commit -m "feat: admin guards, _require_admin helper, /usuarios command"
```

---

## Verificación final en Telegram

1. `/usuarios` → debe mostrar lista de sesiones (solo si eres admin)
2. Con un chat_id de viewer, `/prospectar giro ciudad` → debe responder "⛔ Este comando es solo para administradores."
3. `/ayuda` → debe incluir `/usuarios`
4. Suite completa: `docker exec chatbot-backend-1 python -m pytest core/agent/tests/ -q`
