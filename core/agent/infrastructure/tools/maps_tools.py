import logging
import requests
import django_rq
from django.conf import settings
from core.agent.domain.tools import BaseTool, ToolResult

logger = logging.getLogger(__name__)


def geocode_location(location: str) -> tuple[float, float] | None:
    """Convierte nombre de ciudad a (lat, lng) usando Google Geocoding API."""
    api_key = getattr(settings, 'GOOGLE_PLACES_API_KEY', '')
    if not api_key:
        return None
    try:
        resp = requests.get(
            'https://maps.googleapis.com/maps/api/geocode/json',
            params={'address': location, 'key': api_key},
            timeout=10,
        )
        data = resp.json()
        if data.get('status') == 'OK' and data.get('results'):
            loc = data['results'][0]['geometry']['location']
            return loc['lat'], loc['lng']
    except Exception as e:
        logger.error(f"Error geocodificando '{location}': {e}")
    return None


class ProspectMapsTool(BaseTool):
    name = 'prospect_maps'

    def execute(self, giro: str, location: str, rango_km: float = 5.0, chat_id: int = None) -> ToolResult:
        """
        Inicia una prospección asíncrona via n8n.
        Retorna inmediatamente; el resultado llega por Telegram cuando n8n termina.
        """
        # Límite de tasa: máx 3 prospecciones por hora por usuario
        if chat_id and not self._check_rate_limit(chat_id):
            return self._error(
                "Alcanzaste el límite de 3 prospecciones por hora. Intenta más tarde."
            )

        # Resolver coordenadas
        coords = self._parse_or_geocode(location)
        if coords is None:
            return self._error(
                f"No pude encontrar las coordenadas de '{location}'. "
                "Prueba con un nombre de ciudad diferente o usa formato lat,lng (ej: 25.67,-100.31)."
            )

        lat, lng = coords

        # Encolar job en RQ
        try:
            queue = django_rq.get_queue('default')
            queue.enqueue(
                'core.agent.infrastructure.jobs.prospect_n8n_job',
                giro=giro,
                lat=lat,
                lng=lng,
                rango_km=rango_km,
                chat_id=chat_id,
                job_timeout=400,
            )
        except Exception as e:
            logger.error(f"Error encolar job prospección: {e}", exc_info=True)
            return self._error(f"No pude iniciar la prospección: {e}")

        location_label = f"{lat:.4f},{lng:.4f}" if location == f"{lat},{lng}" else location
        return ToolResult(
            content=(
                f"🔍 *Prospección iniciada*\n\n"
                f"• Giro: *{giro}*\n"
                f"• Zona: *{location_label}*\n"
                f"• Radio: *{rango_km} km*\n\n"
                f"Este proceso puede tardar 2-5 minutos.\n"
                f"Te aviso aquí cuando los resultados estén listos en tu Google Sheet."
            ),
            tool_name=self.name,
            success=True,
            metadata={'giro': giro, 'lat': lat, 'lng': lng, 'rango_km': rango_km},
        )

    def _parse_or_geocode(self, location: str) -> tuple[float, float] | None:
        """Detecta si es 'lat,lng' o un nombre de ciudad."""
        parts = location.replace(' ', '').split(',')
        if len(parts) == 2:
            try:
                return float(parts[0]), float(parts[1])
            except ValueError:
                pass
        return geocode_location(location)

    def _check_rate_limit(self, chat_id: int) -> bool:
        from django.core.cache import cache
        key = f"prospect_rate:{chat_id}"
        count = cache.get(key, 0)
        if count >= 3:
            return False
        cache.set(key, count + 1, timeout=3600)
        return True
