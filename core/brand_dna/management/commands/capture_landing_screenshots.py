import os
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

_SCREENSHOTS = {
    'dashboard': '/dashboard/',
    'calendar': '/calendar/{job_id}/',
}

_OUTPUT_DIR = os.path.join(
    settings.BASE_DIR, 'core', 'brand_dna', 'static', 'brand_dna', 'img', 'screenshots',
)


class Command(BaseCommand):
    help = (
        'Captura screenshots reales de la app (dashboard + calendario) con una cuenta demo, '
        'via Playwright, para usarlos como prueba visual en la landing. No renderiza en vivo — '
        'genera PNGs estaticos que se sirven como cualquier otro asset.'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--base-url', default=None,
            help='URL base contra la que corre Playwright (default: settings.COSMIC_BASE_URL).',
        )
        parser.add_argument(
            '--email', default=None,
            help='Correo de la cuenta demo (default: env DEMO_ACCOUNT_EMAIL).',
        )
        parser.add_argument(
            '--password', default=None,
            help='Password de la cuenta demo (default: env DEMO_ACCOUNT_PASSWORD).',
        )
        parser.add_argument(
            '--business-name', default='Tu Web MX',
            help='Nombre del negocio (BrandDNA.business_name) cuyo calendario se captura.',
        )

    def handle(self, *args, **options):
        from playwright.sync_api import sync_playwright
        from core.brand_dna.models import AnalysisJob

        base_url = (options['base_url'] or settings.COSMIC_BASE_URL).rstrip('/')
        email = options['email'] or os.environ.get('DEMO_ACCOUNT_EMAIL')
        password = options['password'] or os.environ.get('DEMO_ACCOUNT_PASSWORD')
        if not email or not password:
            raise CommandError(
                'Faltan credenciales de la cuenta demo. Pasa --email/--password o define '
                'DEMO_ACCOUNT_EMAIL/DEMO_ACCOUNT_PASSWORD en el entorno.'
            )

        # El job debe pertenecer a la MISMA cuenta con la que hacemos login — calendar_review_view
        # exige job.user == request.user, así que un job de otro usuario daria 404 al capturar.
        job = (
            AnalysisJob.objects.filter(
                user__email=email,
                brand_dna__business_name=options['business_name'],
                status=AnalysisJob.STATUS_DONE,
                deleted_at__isnull=True,
            )
            .order_by('-created_at')
            .first()
        )
        if job is None:
            raise CommandError(
                f'No hay un AnalysisJob completado para "{options["business_name"]}" que '
                f'pertenezca a la cuenta {email}. Ese calendario debe estar bajo la MISMA '
                'cuenta con la que se hace login (o pasa --business-name con un negocio que '
                'sí tenga esa cuenta).'
            )

        os.makedirs(_OUTPUT_DIR, exist_ok=True)

        with sync_playwright() as pw:
            browser = pw.chromium.launch(
                headless=True,
                args=['--no-sandbox', '--disable-setuid-sandbox', '--disable-dev-shm-usage'],
            )
            page = browser.new_page(viewport={'width': 1280, 'height': 900})
            try:
                self._login(page, base_url, email, password)
                self._capture(page, f'{base_url}/dashboard/', 'dashboard.png')
                self._capture(page, f'{base_url}/calendar/{job.id}/', 'calendar.png')
            finally:
                browser.close()

        self.stdout.write(self.style.SUCCESS(f'Screenshots guardados en {_OUTPUT_DIR}'))

    def _login(self, page, base_url, email, password):
        page.goto(f'{base_url}/auth/login/', wait_until='load')
        page.fill('#email', email)
        page.fill('#password', password)
        page.click('button[type="submit"]')
        page.wait_for_load_state('networkidle')
        if '/auth/login/' in page.url:
            error_box = page.query_selector('.error-box')
            detail = error_box.inner_text().strip() if error_box else '(sin mensaje de error visible en la pagina)'
            raise CommandError(
                f'Login fallo: {detail}\n'
                f'  base-url usado: {base_url}\n'
                '  Si esto corre desde el servidor de desarrollo, --base-url por defecto apunta a '
                'COSMIC_BASE_URL (produccion, base de datos SEPARADA de este servidor). Las '
                'credenciales de DEMO_ACCOUNT_EMAIL/PASSWORD deben pertenecer a la MISMA base de '
                'datos que --base-url. Para probar contra este servidor de dev, pasa explicito: '
                '--base-url https://deploy.anuarbarrera.dev'
            )

    def _capture(self, page, url, filename):
        page.goto(url, wait_until='load')
        # Las imagenes de los posts usan loading="lazy" (solo cargan si entran al viewport,
        # y las secciones colapsadas del acordeon de semanas nunca las disparan). Forzamos
        # "eager" en todas para que el navegador las pida de inmediato, sin depender de scroll.
        page.evaluate(
            "document.querySelectorAll('img[loading=\"lazy\"]').forEach(img => img.loading = 'eager')"
        )
        try:
            page.wait_for_function(
                'Array.from(document.images).every(img => img.complete && img.naturalWidth > 0)',
                timeout=20000,
            )
        except Exception:
            self.stdout.write(self.style.WARNING(
                f'  {filename}: alguna imagen no termino de cargar a tiempo, la captura puede tener huecos'
            ))
        out_path = os.path.join(_OUTPUT_DIR, filename)
        page.screenshot(path=out_path, full_page=True)
        self.stdout.write(f'  {filename} <- {url}')
