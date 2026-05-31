from django.http import JsonResponse
from django.views.decorators.http import require_GET
from django.conf import settings
from django.db.models import Count, Avg, Sum, Q
from django.utils import timezone
from datetime import timedelta
from core.agent.infrastructure.models import AgentSession, AgentRequest


@require_GET
def agent_health(request):
    return JsonResponse({
        "status": "ok",
        "model": getattr(settings, 'AI_MODEL', 'gemini-2.5-flash'),
        "telegram": bool(getattr(settings, 'TELEGRAM_BOT_TOKEN', '')),
    })


@require_GET
def agent_metrics(request):
    since = timezone.now() - timedelta(days=30)
    qs = AgentRequest.objects.filter(timestamp__gte=since)

    data = qs.aggregate(
        total_requests=Count('id'),
        successful=Count('id', filter=Q(success=True)),
        avg_duration_ms=Avg('duration_ms'),
        total_tokens=Sum('estimated_tokens'),
    )

    sessions = AgentSession.objects.filter(is_authorized=True).count()

    return JsonResponse({
        "period_days": 30,
        "sessions_authorized": sessions,
        **{k: (round(v, 2) if isinstance(v, float) else v or 0) for k, v in data.items()},
    })
