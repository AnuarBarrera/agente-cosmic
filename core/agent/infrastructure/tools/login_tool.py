import logging
import asyncio
from django.conf import settings
from core.agent.domain.tools import BaseTool, ToolResult

logger = logging.getLogger(__name__)

PLATFORM_LOGIN_URLS = {
    'instagram': 'https://www.instagram.com/accounts/login/',
    'tiktok': 'https://www.tiktok.com/login',
    'facebook': 'https://www.facebook.com/login',
    'linkedin': 'https://www.linkedin.com/login',
    'twitter': 'https://twitter.com/login',
}

PLATFORM_SUCCESS_URLS = {
    'instagram': 'instagram.com/',
    'tiktok': 'tiktok.com/foryou',
    'facebook': 'facebook.com',
    'linkedin': 'linkedin.com/feed',
    'twitter': 'twitter.com/home',
}


class BrowserLoginTool(BaseTool):
    name = 'browser_login'

    def execute(self, platform: str, username: str, password: str, chat_id: int = None) -> ToolResult:
        try:
            result = asyncio.run(self._login_async(platform, username, password))
            return result
        except Exception as e:
            logger.error(f"Error en login {platform}: {e}", exc_info=True)
            return self._error(f"Error al iniciar sesión en {platform}: {e}")

    async def _login_async(self, platform: str, username: str, password: str) -> ToolResult:
        from playwright.async_api import async_playwright
        from core.agent.infrastructure.browser import CHROME_ARGS, USER_AGENT

        login_url = PLATFORM_LOGIN_URLS.get(platform)
        if not login_url:
            return self._error(f"Plataforma '{platform}' no soportada para login.")

        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True, args=CHROME_ARGS)
            context = await browser.new_context(
                user_agent=USER_AGENT,
                viewport={'width': 1280, 'height': 720},
            )
            page = await context.new_page()
            await page.add_init_script(
                "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
            )

            try:
                success, cookies = await self._do_login(page, platform, username, password)
            finally:
                await browser.close()

        if success and cookies:
            self._save_session(platform, username, cookies, USER_AGENT)
            return ToolResult(
                content=(
                    f"✅ *Sesión iniciada en {platform.capitalize()}*\n\n"
                    f"Tu sesión está guardada y se usará automáticamente "
                    f"cuando consultes estadísticas de {platform.capitalize()}.\n\n"
                    f"La sesión dura aproximadamente 30 días."
                ),
                tool_name=self.name,
                success=True,
                metadata={'platform': platform, 'username': username},
            )
        else:
            return self._error(
                f"No pude iniciar sesión en {platform.capitalize()}. "
                "Verifica que las credenciales sean correctas o que no haya 2FA activo."
            )

    async def _do_login(self, page, platform: str, username: str, password: str):
        """Lógica de login específica por plataforma."""
        try:
            if platform == 'instagram':
                return await self._login_instagram(page, username, password)
            elif platform == 'tiktok':
                return await self._login_tiktok(page, username, password)
            elif platform in ('twitter', 'x'):
                return await self._login_twitter(page, username, password)
            else:
                return False, []
        except Exception as e:
            logger.error(f"Error en _do_login({platform}): {e}")
            return False, []

    async def _login_instagram(self, page, username: str, password: str):
        # Instagram detecta headless agresivamente — navegamos a home primero
        await page.goto('https://www.instagram.com/', timeout=30000)
        await page.wait_for_timeout(3000)

        # Intentar múltiples selectores que Instagram usa según el contexto
        username_selectors = [
            'input[name="username"]',
            'input[aria-label="Phone number, username, or email"]',
            'input[autocomplete="username"]',
            'form input[type="text"]',
        ]

        field_found = False
        for sel in username_selectors:
            try:
                await page.wait_for_selector(sel, timeout=8000)
                await page.fill(sel, username)
                field_found = True
                break
            except Exception:
                continue

        if not field_found:
            # Intentar ir directo al login
            await page.goto('https://www.instagram.com/accounts/login/', timeout=30000)
            await page.wait_for_timeout(3000)
            for sel in username_selectors:
                try:
                    await page.wait_for_selector(sel, timeout=8000)
                    await page.fill(sel, username)
                    field_found = True
                    break
                except Exception:
                    continue

        if not field_found:
            return False, []

        password_selectors = [
            'input[name="password"]',
            'input[type="password"]',
            'input[aria-label="Password"]',
        ]
        for sel in password_selectors:
            try:
                await page.fill(sel, password)
                break
            except Exception:
                continue

        # Simular comportamiento humano antes de submit
        await page.wait_for_timeout(1000)
        await page.keyboard.press('Tab')
        await page.wait_for_timeout(500)

        submit_selectors = [
            'button[type="submit"]',
            'button:has-text("Log in")',
            'button:has-text("Iniciar sesión")',
        ]
        for sel in submit_selectors:
            try:
                await page.click(sel)
                break
            except Exception:
                continue

        await page.wait_for_timeout(5000)

        # Verificar login exitoso (URL cambia a home o feed)
        current_url = page.url.lower()
        if 'login' not in current_url and 'challenge' not in current_url:
            cookies = await page.context.cookies()
            return bool(cookies), cookies

        return False, []

    async def _login_tiktok(self, page, username: str, password: str):
        await page.goto('https://www.tiktok.com/login/phone-or-email/email', timeout=20000)
        await page.wait_for_timeout(2000)
        await page.fill('input[name="username"]', username)
        await page.fill('input[type="password"]', password)
        await page.click('button[type="submit"]')
        await page.wait_for_timeout(4000)

        if 'login' not in page.url.lower():
            cookies = await page.context.cookies()
            return True, cookies
        return False, []

    async def _login_twitter(self, page, username: str, password: str):
        await page.goto('https://twitter.com/login', timeout=20000)
        await page.wait_for_selector('input[autocomplete="username"]', timeout=10000)
        await page.fill('input[autocomplete="username"]', username)
        await page.click('div[data-testid="LoginForm_Login_Button"]')
        await page.wait_for_timeout(2000)
        await page.fill('input[type="password"]', password)
        await page.click('div[data-testid="LoginForm_Login_Button"]')
        await page.wait_for_timeout(4000)

        if 'login' not in page.url.lower():
            cookies = await page.context.cookies()
            return True, cookies
        return False, []

    def _save_session(self, platform: str, username: str, cookies: list, user_agent: str):
        from core.agent.infrastructure.models import BrowserSession
        BrowserSession.objects.update_or_create(
            platform=platform,
            username=username,
            defaults={
                'cookies': cookies,
                'user_agent': user_agent,
                'is_valid': True,
            },
        )
