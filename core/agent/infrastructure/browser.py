"""
Playwright wrapper para scraping de estadísticas en redes sociales.
Diseñado para correr dentro de RQ jobs (sync → async via asyncio.run).
"""
import asyncio
import logging
import re
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)

CHROME_ARGS = [
    '--no-sandbox',
    '--disable-setuid-sandbox',
    '--disable-dev-shm-usage',
    '--disable-blink-features=AutomationControlled',
    '--disable-infobars',
    '--window-size=1920,1080',
]

USER_AGENT = (
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
    'AppleWebKit/537.36 (KHTML, like Gecko) '
    'Chrome/120.0.0.0 Safari/537.36'
)


@dataclass
class PostStats:
    platform: str
    url: str
    likes: Optional[str] = None
    comments: Optional[str] = None
    shares: Optional[str] = None
    views: Optional[str] = None
    saves: Optional[str] = None
    title: Optional[str] = None
    author: Optional[str] = None
    description: Optional[str] = None
    raw_meta: dict = field(default_factory=dict)
    error: Optional[str] = None

    @property
    def success(self) -> bool:
        return self.error is None and any([
            self.likes, self.comments, self.views, self.shares
        ])

    def format_telegram(self) -> str:
        lines = [f"📊 *Estadísticas — {self.platform.capitalize()}*\n"]
        if self.author:
            lines.append(f"👤 Autor: {self.author}")
        if self.title:
            lines.append(f"📝 {self.title[:100]}")
        lines.append("")
        if self.views:
            lines.append(f"👁 Vistas: *{self.views}*")
        if self.likes:
            lines.append(f"❤️ Likes: *{self.likes}*")
        if self.comments:
            lines.append(f"💬 Comentarios: *{self.comments}*")
        if self.shares:
            lines.append(f"🔁 Compartidos: *{self.shares}*")
        if self.saves:
            lines.append(f"🔖 Guardados: *{self.saves}*")
        if not any([self.views, self.likes, self.comments, self.shares]):
            lines.append("_No se encontraron estadísticas públicas._")
            lines.append("Esta red puede requerir inicio de sesión para ver las métricas.")
        return '\n'.join(lines)


def detect_platform(url: str) -> str:
    url = url.lower()
    if 'instagram.com' in url:
        return 'instagram'
    if 'tiktok.com' in url:
        return 'tiktok'
    if 'facebook.com' in url or 'fb.com' in url:
        return 'facebook'
    if 'linkedin.com' in url:
        return 'linkedin'
    if 'twitter.com' in url or 'x.com' in url:
        return 'twitter'
    return 'unknown'


def scrape_post_stats(url: str, cookies: list = None) -> PostStats:
    """Entry point síncrono para RQ jobs."""
    platform = detect_platform(url)
    try:
        return asyncio.run(_scrape_async(url, platform, cookies or []))
    except Exception as e:
        logger.error(f"Error scraping {url}: {e}", exc_info=True)
        return PostStats(platform=platform, url=url, error=str(e))


def _normalize_cookies(cookies: list) -> list:
    """Normaliza cookies exportadas por Cookie-Editor al formato que acepta Playwright."""
    same_site_map = {
        'no_restriction': 'None',
        'lax': 'Lax',
        'strict': 'Strict',
        'none': 'None',
        'unspecified': 'Lax',
        '': 'Lax',
    }
    result = []
    for c in cookies:
        c = dict(c)
        raw = str(c.get('sameSite', '')).lower()
        c['sameSite'] = same_site_map.get(raw, 'Lax')
        # Playwright requiere 'name' y 'value'; ignorar cookies sin ellos
        if c.get('name') and c.get('value') is not None:
            result.append(c)
    return result


async def _scrape_async(url: str, platform: str, cookies: list) -> PostStats:
    from playwright.async_api import async_playwright

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True, args=CHROME_ARGS)
        context = await browser.new_context(
            user_agent=USER_AGENT,
            viewport={'width': 1920, 'height': 1080},
            locale='es-MX',
        )

        if cookies:
            await context.add_cookies(_normalize_cookies(cookies))

        page = await context.new_page()
        # Evitar detección de automatización
        await page.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
        )

        try:
            await page.goto(url, timeout=30000, wait_until='domcontentloaded')
            await page.wait_for_timeout(2000)

            scraper = _get_scraper(platform)
            stats = await scraper(page, url, platform)
        except Exception as e:
            stats = PostStats(platform=platform, url=url, error=str(e))
        finally:
            await browser.close()

    return stats


def _get_scraper(platform: str):
    scrapers = {
        'tiktok': _scrape_tiktok,
        'instagram': _scrape_instagram,
        'facebook': _scrape_facebook,
        'linkedin': _scrape_linkedin,
        'twitter': _scrape_twitter,
    }
    return scrapers.get(platform, _scrape_generic)


async def _scrape_tiktok(page, url: str, platform: str) -> PostStats:
    stats = PostStats(platform=platform, url=url)
    try:
        await page.wait_for_selector('[data-e2e="like-count"], [data-e2e="browse-like-count"]', timeout=8000)
    except Exception:
        pass

    async def _text(selectors):
        for sel in selectors:
            try:
                el = await page.query_selector(sel)
                if el:
                    return (await el.text_content() or '').strip()
            except Exception:
                pass
        return None

    stats.likes = await _text(['[data-e2e="like-count"]', '[data-e2e="browse-like-count"]'])
    stats.comments = await _text(['[data-e2e="comment-count"]', '[data-e2e="browse-comment-count"]'])
    stats.shares = await _text(['[data-e2e="share-count"]'])
    stats.views = await _text(['[data-e2e="video-views"]', 'strong[data-e2e="video-views"]'])

    # Author y descripción desde meta tags
    meta = await _get_meta(page)
    stats.author = meta.get('og:title', '').split(' on TikTok')[0] or None
    stats.description = meta.get('og:description') or None
    stats.raw_meta = meta
    return stats


async def _scrape_instagram(page, url: str, platform: str) -> PostStats:
    stats = PostStats(platform=platform, url=url)

    # Esperar renderizado completo (especialmente importante con cookies)
    try:
        await page.wait_for_load_state('networkidle', timeout=8000)
    except Exception:
        pass

    # Detectar redirección a login (cookies expiradas o inválidas)
    current_url = page.url
    if 'accounts/login' in current_url or 'accounts/emailsignup' in current_url:
        stats.error = "Cookies de Instagram expiradas. Usa /importcookies instagram para renovarlas."
        return stats

    meta = await _get_meta(page)
    stats.raw_meta = meta
    desc = meta.get('og:description', '')
    stats.author = meta.get('og:title', '').split(' on Instagram')[0] or None
    stats.description = desc[:200] if desc else None

    # og:description a veces contiene counts en posts públicos
    likes_match = re.search(r'([\d,\.]+)\s*(?:likes?|me gustas?)', desc, re.IGNORECASE)
    comments_match = re.search(r'([\d,\.]+)\s*(?:comments?|comentarios?)', desc, re.IGNORECASE)
    if likes_match:
        stats.likes = likes_match.group(1)
    if comments_match:
        stats.comments = comments_match.group(1)

    # JSON embebido en la página (patrones modernos y legacy)
    if not stats.likes or not stats.comments or not stats.views:
        try:
            content = await page.content()
            if not stats.likes:
                m = re.search(r'"like_count"\s*:\s*(\d+)', content)
                if not m:
                    m = re.search(r'"edge_liked_by":\{"count":(\d+)\}', content)
                if m:
                    stats.likes = m.group(1)
            if not stats.comments:
                m = re.search(r'"comment_count"\s*:\s*(\d+)', content)
                if not m:
                    m = re.search(r'"edge_media_to_parent_comment":\{"count":(\d+)\}', content)
                if m:
                    stats.comments = m.group(1)
            if not stats.views:
                m = re.search(r'"play_count"\s*:\s*(\d+)', content)
                if not m:
                    m = re.search(r'"video_view_count"\s*:\s*(\d+)', content)
                if m:
                    stats.views = m.group(1)
        except Exception:
            pass

    # DOM: buscar "Ver los X comentarios" visible cuando hay sesión activa
    if not stats.comments:
        try:
            for sel in ['a[href*="comments"]', 'span[aria-label*="comment"]', 'button[type="button"] span']:
                els = await page.query_selector_all(sel)
                for el in els:
                    text = (await el.text_content() or '').strip()
                    m = re.search(r'(\d[\d,\.]*)\s*(?:comments?|comentarios?)', text, re.IGNORECASE)
                    if m:
                        stats.comments = m.group(1)
                        break
                if stats.comments:
                    break
        except Exception:
            pass

    if not stats.success:
        stats.error = (
            "No se encontraron métricas. Instagram limita el acceso incluso con sesión. "
            "Prueba con otra URL o renueva las cookies con /importcookies instagram."
        )
    return stats


async def _scrape_facebook(page, url: str, platform: str) -> PostStats:
    stats = PostStats(platform=platform, url=url)
    meta = await _get_meta(page)
    stats.raw_meta = meta
    stats.description = meta.get('og:description', '')[:200] or None
    stats.author = meta.get('og:title', '') or None

    # Facebook requiere login para la mayoría de las métricas
    stats.error = (
        "Facebook requiere inicio de sesión para ver estadísticas. "
        "Próximamente: comando /login facebook."
    )
    return stats


async def _scrape_linkedin(page, url: str, platform: str) -> PostStats:
    stats = PostStats(platform=platform, url=url)
    try:
        await page.wait_for_selector('.social-counts-reactions', timeout=6000)
        reactions = await page.query_selector('.social-counts-reactions__count')
        if reactions:
            stats.likes = (await reactions.text_content() or '').strip()
    except Exception:
        pass

    meta = await _get_meta(page)
    stats.raw_meta = meta
    stats.description = meta.get('og:description', '')[:200] or None
    return stats


async def _scrape_twitter(page, url: str, platform: str) -> PostStats:
    stats = PostStats(platform=platform, url=url)
    try:
        await page.wait_for_selector('[data-testid="like"]', timeout=8000)
        likes_el = await page.query_selector('[data-testid="like"] span span')
        if likes_el:
            stats.likes = (await likes_el.text_content() or '').strip()
        reply_el = await page.query_selector('[data-testid="reply"] span span')
        if reply_el:
            stats.comments = (await reply_el.text_content() or '').strip()
        retweet_el = await page.query_selector('[data-testid="retweet"] span span')
        if retweet_el:
            stats.shares = (await retweet_el.text_content() or '').strip()
    except Exception:
        pass

    meta = await _get_meta(page)
    stats.raw_meta = meta
    return stats


async def _scrape_generic(page, url: str, platform: str) -> PostStats:
    meta = await _get_meta(page)
    return PostStats(
        platform=platform, url=url,
        description=meta.get('og:description', '')[:200] or None,
        author=meta.get('og:title') or None,
        raw_meta=meta,
        error=f"Plataforma '{platform}' no soportada aún.",
    )


async def _get_meta(page) -> dict:
    """Extrae todos los meta tags og: de la página."""
    try:
        tags = await page.query_selector_all('meta[property^="og:"]')
        result = {}
        for tag in tags:
            prop = await tag.get_attribute('property')
            content = await tag.get_attribute('content')
            if prop and content:
                result[prop] = content
        return result
    except Exception:
        return {}
