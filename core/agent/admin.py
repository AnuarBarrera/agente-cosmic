from django.contrib import admin
from django.db.models import Count, Avg, Sum, Q
from django.utils import timezone
from django.utils.html import format_html
from datetime import timedelta

from .infrastructure.models import AgentSession, AgentMemory, AgentRequest, BrowserSession


def _last_30():
    return timezone.now() - timedelta(days=30)


@admin.register(AgentSession)
class AgentSessionAdmin(admin.ModelAdmin):
    list_display = ('full_name', 'username', 'chat_id', 'is_authorized', 'total_requests', 'last_active_at')
    list_filter = ('is_authorized',)
    search_fields = ('full_name', 'username', 'chat_id')
    readonly_fields = ('created_at', 'last_active_at')

    def get_queryset(self, request):
        return super().get_queryset(request).annotate(
            _total=Count('requests', filter=Q(requests__timestamp__gte=_last_30()))
        )

    def total_requests(self, obj):
        return obj._total
    total_requests.short_description = 'Req (30d)'
    total_requests.admin_order_field = '_total'


@admin.register(AgentMemory)
class AgentMemoryAdmin(admin.ModelAdmin):
    list_display = ('session', 'role', 'short_content', 'timestamp')
    list_filter = ('role', 'session')
    search_fields = ('content',)
    readonly_fields = ('timestamp',)
    date_hierarchy = 'timestamp'
    ordering = ('-timestamp',)

    def short_content(self, obj):
        return obj.content[:80]
    short_content.short_description = 'Contenido'


@admin.register(BrowserSession)
class BrowserSessionAdmin(admin.ModelAdmin):
    list_display = ('platform', 'username', 'is_valid', 'last_used_at')
    list_filter = ('platform', 'is_valid')
    readonly_fields = ('created_at', 'last_used_at')


@admin.register(AgentRequest)
class AgentRequestAdmin(admin.ModelAdmin):
    list_display = (
        'session', 'model_used', 'tool_used',
        'duration_ms', 'estimated_tokens', 'status_badge', 'timestamp',
    )
    list_filter = ('success', 'model_used', 'tool_used')
    search_fields = ('user_message', 'ai_response')
    readonly_fields = ('timestamp',)
    date_hierarchy = 'timestamp'
    ordering = ('-timestamp',)

    def status_badge(self, obj):
        if obj.success:
            return format_html('<span style="color:green">&#10003;</span>')
        return format_html('<span style="color:red" title="{}">&#10007;</span>', obj.error_message or '')
    status_badge.short_description = 'OK'

    def changelist_view(self, request, extra_context=None):
        since = _last_30()
        qs = AgentRequest.objects.filter(timestamp__gte=since)
        agg = qs.aggregate(
            total=Count('id'),
            ok=Count('id', filter=Q(success=True)),
            avg_ms=Avg('duration_ms'),
            total_tokens=Sum('estimated_tokens'),
        )
        total = agg['total'] or 0
        ok = agg['ok'] or 0
        success_rate = round(ok / total * 100, 1) if total else 0
        avg_ms = round(agg['avg_ms'] or 0)
        total_tokens = agg['total_tokens'] or 0

        top_tools = (
            qs.exclude(tool_used=None)
            .values('tool_used')
            .annotate(n=Count('id'))
            .order_by('-n')[:5]
        )

        extra_context = extra_context or {}
        extra_context['metrics'] = {
            'total': total,
            'success_rate': success_rate,
            'avg_ms': avg_ms,
            'total_tokens': total_tokens,
            'top_tools': list(top_tools),
        }
        return super().changelist_view(request, extra_context=extra_context)
