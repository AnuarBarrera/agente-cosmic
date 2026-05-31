import logging
from datetime import datetime
from django.utils import timezone
from django.db.models import Count, Avg, Sum, Q
from core.agent.domain.tools import BaseTool, ToolResult

logger = logging.getLogger(__name__)


class GenerateMonthlyReportTool(BaseTool):
    name = 'generate_monthly_report'

    def execute(self, month: int = None, year: int = None) -> ToolResult:
        from core.agent.infrastructure.models import AgentRequest, AgentMemory, AgentSession

        now = timezone.now()
        month = month or now.month
        year = year or now.year

        try:
            qs = AgentRequest.objects.filter(timestamp__year=year, timestamp__month=month)
            stats = qs.aggregate(
                total=Count('id'),
                exitosas=Count('id', filter=Q(success=True)),
                fallidas=Count('id', filter=Q(success=False)),
                duracion_promedio=Avg('duration_ms'),
                tokens_totales=Sum('estimated_tokens'),
            )

            tools_used = (
                qs.exclude(tool_used__isnull=True)
                .exclude(tool_used='')
                .values('tool_used')
                .annotate(count=Count('id'))
                .order_by('-count')
            )

            sessions = AgentSession.objects.filter(is_authorized=True).count()
            memories = AgentMemory.objects.filter(
                timestamp__year=year, timestamp__month=month
            ).count()

            month_name = datetime(year, month, 1).strftime('%B %Y')
            total = stats['total'] or 0
            exitosas = stats['exitosas'] or 0
            tasa = round((exitosas / total * 100), 1) if total > 0 else 0
            duracion = round(stats['duracion_promedio'] or 0)
            tokens = stats['tokens_totales'] or 0

            tools_lines = '\n'.join(
                f"  • {t['tool_used']}: {t['count']} usos"
                for t in tools_used
            ) or '  • Ninguna herramienta específica usada'

            report = (
                f"📊 *Reporte de {month_name}*\n\n"
                f"💬 *Conversaciones*\n"
                f"  • Total de mensajes: {total}\n"
                f"  • Exitosos: {exitosas} ({tasa}%)\n"
                f"  • Con error: {stats['fallidas'] or 0}\n"
                f"  • Mensajes en memoria: {memories}\n\n"
                f"⚡ *Rendimiento*\n"
                f"  • Tiempo promedio de respuesta: {duracion} ms\n"
                f"  • Tokens estimados consumidos: {tokens:,}\n\n"
                f"🛠 *Herramientas usadas*\n"
                f"{tools_lines}\n\n"
                f"👥 *Sesiones autorizadas activas:* {sessions}"
            )

            return ToolResult(
                content=report,
                tool_name=self.name,
                success=True,
                metadata={'month': month, 'year': year, 'total_requests': total},
            )
        except Exception as e:
            logger.error(f"Error en GenerateMonthlyReportTool: {e}", exc_info=True)
            return self._error(f"No pude generar el reporte: {e}")
