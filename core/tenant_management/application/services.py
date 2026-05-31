import uuid
import logging

from ..domain.entities import Tenant, TenantConfiguration, Subscription, PlanName

logger = logging.getLogger(__name__)

from ..domain.repositories import TenantRepository, UsageRecordRepository
from ..domain.services import UsageTrackingService
from ..infrastructure.repositories import DjangoUsageRecordRepository
from .commands import (
    RegisterTenantCommand,
    UpdateConfigurationCommand,
    UpdateAIConfigurationCommand,
)
from .dtos import TenantDTO, UsageRecordDTO
from core.shared.event_bus import EventBus, register_handler
from core.shared.events import PlanChanged, UsageLimitExceeded, GeminiApiRateLimitExceeded


class TenantApplicationService:
    def __init__(self, tenant_repository: TenantRepository):
        self.tenant_repository = tenant_repository
        self.usage_record_repository = DjangoUsageRecordRepository()
        self.usage_tracking_service = UsageTrackingService(self.tenant_repository, self.usage_record_repository)

    def register_tenant(self, command: RegisterTenantCommand) -> TenantDTO:
        # Obtener el plan "Free"
        free_plan = self.tenant_repository.get_plan_by_name(PlanName.FREE.value)
        if not free_plan:
            # Esto podría ser un error fatal si el plan Free no está en la BD
            raise ValueError("Free plan not found. Please seed the database with plans.")

        # Crear la suscripción por defecto
        default_subscription = Subscription(plan=free_plan)

        # Crear un nuevo tenant usando el método de dominio
        new_tenant = Tenant.register(name=command.name, subscription=default_subscription)

        # Guardar el nuevo tenant
        self.tenant_repository.save(new_tenant)

        # Devolver un DTO
        return TenantDTO(
            tenant_id=new_tenant.id,
            name=new_tenant.name,
            plan=new_tenant.subscription.plan.name.value if new_tenant.subscription else None,
            status=new_tenant.status.value,
            configuration=new_tenant.configuration.to_dict() if new_tenant.configuration else {}
        )

    def update_configuration(
        self,
        command: UpdateConfigurationCommand,
    ) -> None:
        tenant = self.tenant_repository.get_by_id(command.tenant_id)
        if not tenant:
            raise ValueError("Tenant not found")

        new_config = TenantConfiguration(
            tenant_id=tenant.id,
            ai_settings=command.ai_settings,
            channel_settings=command.channel_settings,
            routing_rules=command.routing_rules,
        )

        tenant.update_configuration(new_config)
        self.tenant_repository.save(tenant)

    def get_tenant(self, tenant_id: uuid.UUID) -> TenantDTO | None:
        tenant = self.tenant_repository.get_by_id(tenant_id)
        if tenant:
            return TenantDTO(
                tenant_id=tenant.id,
                name=tenant.name,
                plan=tenant.subscription.plan.name.value if tenant.subscription else None,
                status=tenant.status.value,
                configuration=tenant.configuration.to_dict() if tenant.configuration else {}
            )
        return None

    def list_tenants(self) -> list[TenantDTO]:
        tenants = self.tenant_repository.list()
        return [
            TenantDTO(
                tenant_id=t.id,
                name=t.name,
                plan=t.subscription.plan.name.value if t.subscription else None,
                status=t.status.value,
                configuration=t.configuration.to_dict() if t.configuration else {}
            )
            for t in tenants
        ]

    def suspend_tenant(self, tenant_id: uuid.UUID) -> None:
        tenant = self.tenant_repository.get_by_id(tenant_id)
        if not tenant:
            raise ValueError("Tenant not found")

        tenant.suspend()
        self.tenant_repository.save(tenant)

    def update_ai_configuration(
        self,
        command: UpdateAIConfigurationCommand,
    ) -> None:
        tenant = self.tenant_repository.get_by_id(command.tenant_id)
        if not tenant:
            raise ValueError("Tenant not found")

        # Create a new configuration object based on the existing one
        new_config = tenant.configuration
        new_config.ai_settings = command.ai_settings

        tenant.update_configuration(new_config)
        self.tenant_repository.save(tenant)

    def can_perform_interaction(self, tenant_id: uuid.UUID) -> bool:
        return self.usage_tracking_service.can_perform_interaction(tenant_id)

    def record_interaction(self, tenant_id: uuid.UUID) -> None:
        """
        Records an interaction for a given tenant.
        """
        self.usage_tracking_service.record_interaction(tenant_id)

    def change_tenant_plan(self, tenant_id: uuid.UUID, new_plan_name: str) -> None:
        tenant = self.tenant_repository.get_by_id(tenant_id)
        if not tenant:
            raise ValueError("Tenant not found")

        old_plan_name = tenant.subscription.plan.name.value if tenant.subscription else "None"

        new_plan = self.tenant_repository.get_plan_by_name(new_plan_name)
        if not new_plan:
            raise ValueError(f"Plan '{new_plan_name}' not found")

        tenant.change_subscription(new_plan)
        self.tenant_repository.save(tenant)

        # Publish the domain event
        EventBus.publish(
            PlanChanged(
                tenant_id=tenant.id,
                old_plan_name=old_plan_name,
                new_plan_name=new_plan.name.value
            )
        )

    def get_tenant_usage(self, tenant_id: uuid.UUID) -> UsageRecordDTO:
        """
        Retrieves the daily and monthly usage for a given tenant.
        """
        from django.utils import timezone
        from datetime import time

        now = timezone.now()
        start_of_day = now.replace(hour=0, minute=0, second=0, microsecond=0)
        start_of_month = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

        daily_usage = self.usage_record_repository.get_usage_since(tenant_id, start_of_day)
        monthly_usage = self.usage_record_repository.get_monthly_usage_count(tenant_id, start_of_month)

        return UsageRecordDTO(
            total_daily=daily_usage,
            total_monthly=monthly_usage
        )

    def get_dashboard_data(self, tenant_id: uuid.UUID) -> "DashboardDataDTO":
        """
        Retrieves and calculates the data for the tenant's dashboard.
        """
        from .dtos import DashboardDataDTO
        from core.conversation_management.infrastructure.models import ConversationModel, MessageModel
        from core.routing_escalation.infrastructure.models import EscalationCaseModel, EscalationLogModel
        from core.channel_integration.infrastructure.models import ChannelModel
        from django.db.models import Count, Avg, F, Subquery, OuterRef, Q
        import datetime

        logger.info(f"Dashboard: Starting data retrieval for tenant_id: {tenant_id}")

        # Ensure tenant_id is a UUID object for consistent querying
        tenant_uuid = uuid.UUID(str(tenant_id))
        
        # Define AI sender types for consistent use throughout the method
        possible_ai_senders = ['ai', 'agent', 'bot', 'assistant', 'system', 'AI', 'USER']

        # 1. Total Conversations
        total_conversations = ConversationModel.objects.filter(tenant_id=tenant_uuid).count()
        logger.info(f"Dashboard: Found {total_conversations} total conversations for tenant {tenant_uuid}.")

        # 2. Casos Resueltos: Lógica basada en los datos reales
        # USER parece ser respuestas del sistema/bot, customer son mensajes del cliente
        
        # Conversaciones con respuestas automatizadas (USER = sistema)
        successful_ai_responses = ConversationModel.objects.filter(
            tenant_id=tenant_uuid,
            messages__sender='USER'
        ).distinct().count()
        
        # Verificar si EscalationLogModel existe y tiene datos
        try:
            successful_rule_operations = EscalationLogModel.objects.filter(
                tenant_id=tenant_uuid
            ).values('message_id').distinct().count()
        except Exception as e:
            logger.warning(f"Dashboard: EscalationLogModel query failed: {e}")
            successful_rule_operations = 0
        
        # Casos de escalación resueltos
        resolved_escalation_cases = EscalationCaseModel.objects.filter(
            tenant_id=tenant_uuid, 
            status='RESOLVED'
        ).count()
        
        # Casos resueltos = respuestas del sistema + operaciones de reglas + escalaciones resueltas
        resolved_cases = successful_ai_responses + successful_rule_operations + resolved_escalation_cases
            
        logger.info(f"Dashboard: Calculated resolved cases: {resolved_cases} (System responses: {successful_ai_responses}, Rules: {successful_rule_operations}, Escalations: {resolved_escalation_cases})")

        # 3. Conversations by Channel - Corregir la lógica para coincidir con total_conversations
        channel_conversations = ConversationModel.objects.filter(
            tenant_id=tenant_uuid
        ).values('channel_id').annotate(count=Count('id'))
        
        # Incluir conversaciones sin canal asignado
        conversations_without_channel = ConversationModel.objects.filter(
            tenant_id=tenant_uuid,
            channel_id__isnull=True
        ).count()
        
        channel_ids = [item['channel_id'] for item in channel_conversations if item['channel_id'] is not None]
        channels = ChannelModel.objects.filter(id__in=channel_ids).in_bulk()

        conversations_by_channel = {}
        total_accounted = 0
        
        for item in channel_conversations:
            if item['channel_id'] is not None:
                channel = channels.get(item['channel_id'])
                channel_type = channel.type if channel else 'Unknown'
                conversations_by_channel[channel_type] = conversations_by_channel.get(channel_type, 0) + item['count']
                total_accounted += item['count']
        
        # Añadir conversaciones sin canal
        if conversations_without_channel > 0:
            conversations_by_channel['No Channel'] = conversations_without_channel
            total_accounted += conversations_without_channel
            
        logger.info(f"Dashboard: Conversations by channel: {conversations_by_channel} (Total: {total_accounted})")

        # 4. Recent Activity
        recent_conversations = ConversationModel.objects.filter(tenant_id=tenant_uuid).order_by('-created_at')[:5]
        
        recent_channel_ids = [conv.channel_id for conv in recent_conversations if conv.channel_id]
        recent_channels = ChannelModel.objects.filter(id__in=recent_channel_ids).in_bulk()

        recent_activity = []
        for conv in recent_conversations:
            channel = recent_channels.get(conv.channel_id) if conv.channel_id else None
            channel_type = channel.type if channel else 'Unknown'
            recent_activity.append({
                "id": str(conv.id),
                "type": "New Conversation",
                "description": f"Started via channel {channel_type}",
                "timestamp": conv.created_at.isoformat()
            })
        logger.info(f"Dashboard: Found {len(recent_activity)} recent activity items.")

        # 5. Automation Rate - Lógica corregida
        automation_rate = 0.0
        if total_conversations > 0:
            # Contar solo conversaciones existentes que fueron escaladas
            escalated_conversations_count = EscalationCaseModel.objects.filter(
                tenant_id=tenant_uuid,
                conversation_id__in=ConversationModel.objects.filter(
                    tenant_id=tenant_uuid
                ).values_list('id', flat=True)
            ).values('conversation_id').distinct().count()
            
            logger.info(f"Dashboard: Total conversations: {total_conversations}, Escalated (existing): {escalated_conversations_count}")
            
            # Contar conversaciones con respuestas automatizadas (USER = respuesta del sistema)
            conversations_with_automation = ConversationModel.objects.filter(
                tenant_id=tenant_uuid,
                messages__sender='USER'  # USER parece ser el sistema/bot
            ).distinct().count()
            
            # Si una conversación tiene escalación Y automatización, priorizar escalación
            escalated_conversation_ids = set(EscalationCaseModel.objects.filter(
                tenant_id=tenant_uuid,
                conversation_id__in=ConversationModel.objects.filter(
                    tenant_id=tenant_uuid
                ).values_list('id', flat=True)
            ).values_list('conversation_id', flat=True))
            
            automated_conversation_ids = set(ConversationModel.objects.filter(
                tenant_id=tenant_uuid,
                messages__sender__in=possible_ai_senders
            ).values_list('id', flat=True))
            
            # Automation rate: Conversaciones que recibieron respuesta automatizada (incluye escaladas que tuvieron respuesta inicial de AI)
            automation_rate = (len(automated_conversation_ids) / total_conversations) * 100
            
            logger.info(f"Dashboard: Automated conversations: {len(automated_conversation_ids)}, Escalated: {len(escalated_conversation_ids)}")
                
        logger.info(f"Dashboard: Calculated automation rate: {automation_rate:.2f}%")

        # 6. Avg. Response Time - Lógica alternativa para estructura de datos actual
        avg_response_time_str = "N/A"
        try:
            conversations = ConversationModel.objects.filter(tenant_id=tenant_uuid).prefetch_related('messages')
            
            conversations_with_both = 0
            conversations_customer_only = 0
            conversations_system_only = 0
            
            # Analizar la estructura de las conversaciones
            for conv in conversations:
                customer_messages = [m for m in conv.messages.all() if m.sender == 'customer']
                system_messages = [m for m in conv.messages.all() if m.sender in possible_ai_senders]
                
                if customer_messages and system_messages:
                    conversations_with_both += 1
                elif customer_messages:
                    conversations_customer_only += 1  
                elif system_messages:
                    conversations_system_only += 1
            
            logger.info(f"Dashboard: Conversation structure - Both: {conversations_with_both}, Customer only: {conversations_customer_only}, System only: {conversations_system_only}")
            
            # Si hay conversaciones bidireccionales, usar esas para el cálculo
            if conversations_with_both > 0:
                total_response_time = datetime.timedelta(0)
                counted_conversations = 0
                debug_count = 0
                
                logger.info(f"Dashboard: Analyzing {conversations_with_both} bidirectional conversations for response time")
                
                for conv in conversations:
                    customer_messages = [m for m in conv.messages.all() if m.sender == 'customer']
                    system_messages = [m for m in conv.messages.all() if m.sender in possible_ai_senders]
                    
                    if customer_messages and system_messages:
                        debug_count += 1
                        # Sort messages by timestamp
                        customer_messages = sorted(customer_messages, key=lambda m: m.timestamp)
                        system_messages = sorted(system_messages, key=lambda m: m.timestamp)
                        
                        # Verificar el patrón de la conversación
                        first_system_ts = system_messages[0].timestamp
                        first_customer_ts = customer_messages[0].timestamp
                        
                        if debug_count <= 3:
                            logger.info(f"Dashboard: Conv {conv.id}: First system: {first_system_ts}, First customer: {first_customer_ts}")
                        
                        # Patrón 1: Sistema responde después del cliente (ideal)
                        system_after_customer = next((m for m in system_messages if m.timestamp > first_customer_ts), None)
                        # Patrón 2: Cliente responde después del sistema (sistema proactivo)
                        customer_after_system = next((m for m in customer_messages if m.timestamp > first_system_ts), None)
                        
                        response_time = None
                        
                        if system_after_customer:
                            # Patrón normal: cliente pregunta -> sistema responde
                            response_time = system_after_customer.timestamp - first_customer_ts
                            if debug_count <= 3:
                                logger.info(f"Dashboard: Conv {conv.id}: Customer->System response time: {response_time.total_seconds()}s")
                        elif customer_after_system and first_system_ts < first_customer_ts:
                            # Patrón proactivo: sistema inicia -> cliente responde
                            # En este caso, calculamos cuán rápido respondió el sistema inicialmente
                            response_time = abs(first_system_ts - conv.created_at)  # Usar valor absoluto
                            if debug_count <= 3:
                                logger.info(f"Dashboard: Conv {conv.id}: Proactive system response time: {response_time.total_seconds()}s")
                        
                        # Contar cualquier tiempo positivo razonable (desde 1ms hasta 1 día)
                        if response_time and datetime.timedelta(milliseconds=1) <= response_time <= datetime.timedelta(days=1):
                            total_response_time += response_time
                            counted_conversations += 1
                            if debug_count <= 3:
                                logger.info(f"Dashboard: Conv {conv.id}: Valid response time: {response_time.total_seconds():.3f}s")
                        elif debug_count <= 3:
                            logger.info(f"Dashboard: Conv {conv.id}: Response time out of range or invalid ({response_time.total_seconds():.6f}s)" if response_time else "No response time calculated")
                
                if counted_conversations > 0:
                    avg_delta = total_response_time / counted_conversations
                    total_seconds = avg_delta.total_seconds()
                    
                    if total_seconds < 1:
                        # Mostrar en milisegundos para tiempos muy pequeños
                        avg_response_time_str = f"{int(total_seconds * 1000)}ms"
                    elif total_seconds < 60:
                        avg_response_time_str = f"{int(total_seconds)}s"
                    elif total_seconds < 3600:
                        minutes, seconds = divmod(int(total_seconds), 60)
                        avg_response_time_str = f"{minutes}m {seconds}s"
                    else:
                        hours, remainder = divmod(int(total_seconds), 3600)
                        minutes, seconds = divmod(remainder, 60)
                        avg_response_time_str = f"{hours}h {minutes}m"
                        
                    logger.info(f"Dashboard: Calculated bidirectional response time: {avg_response_time_str} (from {counted_conversations}/{conversations_with_both} conversations)")
                else:
                    avg_response_time_str = "N/A"
                    logger.info(f"Dashboard: No valid response times found in {conversations_with_both} bidirectional conversations")
                    
            elif conversations_with_both == 0 and conversations_system_only > 0:
                total_response_time = datetime.timedelta(0)
                counted_conversations = 0
                
                for conv in conversations:
                    system_messages = [m for m in conv.messages.all() if m.sender == 'USER']
                    
                    if system_messages:
                        # Usar timestamp de creación de conversación vs primer mensaje del sistema
                        first_system_msg = min(system_messages, key=lambda m: m.timestamp)
                        response_time = first_system_msg.timestamp - conv.created_at
                        
                        # Solo contar si el tiempo es positivo y razonable (menos de 1 día)
                        if datetime.timedelta(0) <= response_time <= datetime.timedelta(days=1):
                            total_response_time += response_time
                            counted_conversations += 1
                
                if counted_conversations > 0:
                    avg_delta = total_response_time / counted_conversations
                    total_seconds = int(avg_delta.total_seconds())
                    
                    if total_seconds < 60:
                        avg_response_time_str = f"{total_seconds}s"
                    elif total_seconds < 3600:
                        minutes, seconds = divmod(total_seconds, 60)
                        avg_response_time_str = f"{minutes}m {seconds}s"
                    else:
                        hours, remainder = divmod(total_seconds, 3600)
                        minutes, seconds = divmod(remainder, 60)
                        avg_response_time_str = f"{hours}h {minutes}m"
                        
                    logger.info(f"Dashboard: Calculated system response time: {avg_response_time_str} (from {counted_conversations} conversations)")
                else:
                    avg_response_time_str = "<1s"
                    logger.info(f"Dashboard: All system responses were immediate")
            else:
                # Lógica original si hay conversaciones bidireccionales
                avg_response_time_str = "N/A"
                logger.info(f"Dashboard: No suitable conversation pattern found for response time calculation")

        except Exception as e:
            logger.error(f"Dashboard: Could not calculate average response time for tenant {tenant_uuid}: {e}", exc_info=True)
            avg_response_time_str = "Error"

        # 7. Porcentaje de respuesta - Lógica más flexible
        response_rate_str = "N/A"
        try:
            # Debug: Verificar qué tipos de sender existen
            all_senders = MessageModel.objects.filter(
                conversation__tenant_id=tenant_uuid
            ).values_list('sender', flat=True).distinct()
            all_senders_list = list(all_senders)
            logger.info(f"Dashboard: All sender types in messages: {all_senders_list}")
            
            # También contar cuántos mensajes hay de cada tipo
            sender_counts = {}
            for sender in all_senders_list:
                count = MessageModel.objects.filter(
                    conversation__tenant_id=tenant_uuid,
                    sender=sender
                ).count()
                sender_counts[sender] = count
            logger.info(f"Dashboard: Sender counts: {sender_counts}")
            
            # Contar conversaciones que tienen respuestas (cualquier tipo que no sea customer)
            conversations_with_responses = ConversationModel.objects.filter(
                tenant_id=tenant_uuid,
                messages__sender__isnull=False
            ).exclude(
                messages__sender='customer'
            ).distinct().count()
            
            # Si eso no funciona, intentemos con valores más específicos conocidos
            if conversations_with_responses == 0:
                # Intentar con diferentes variantes comunes
                conversations_with_responses = ConversationModel.objects.filter(
                    tenant_id=tenant_uuid,
                    messages__sender__in=possible_ai_senders
                ).distinct().count()
            
            if total_conversations > 0:
                response_rate = (conversations_with_responses / total_conversations) * 100
                response_rate_str = f"{response_rate:.1f}%"
            
            logger.info(f"Dashboard: Calculated response rate: {response_rate_str} ({conversations_with_responses}/{total_conversations})")

        except Exception as e:
            logger.error(f"Dashboard: Could not calculate response rate for tenant {tenant_uuid}: {e}", exc_info=True)
            response_rate_str = "Error"

        dto_response = DashboardDataDTO(
            total_conversations=total_conversations,
            resolved_cases=resolved_cases,
            avg_response_time=avg_response_time_str,
            automation_rate=round(automation_rate, 2),
            response_rate=response_rate_str,
            conversations_by_channel=conversations_by_channel,
            recent_activity=recent_activity,
        )
        logger.info(f"Dashboard: Returning DTO: {dto_response}")
        return dto_response

# --- Event Handlers ---
from core.tenant_management.infrastructure.adapters import NotificationServiceAdapter
from core.shared.events import UsageLimitExceeded
from django.conf import settings
from ..infrastructure.repositories import DjangoTenantRepository, DjangoErrorInteractionRepository
from ..models import User

notification_service = NotificationServiceAdapter()
tenant_repository = DjangoTenantRepository()

def handle_usage_limit_exceeded(event: UsageLimitExceeded):
    """
    Handler for the UsageLimitExceeded event.
    Sends a notification to the tenant when their usage limit is exceeded.
    """
    tenant_id = event.tenant_id
    
    # Get the tenant from the repository
    tenant = tenant_repository.get_by_id(tenant_id)
    if not tenant:
        print(f"Error: Tenant {tenant_id} not found when handling usage limit exceeded event.")
        return
    
    # Get the tenant's contact email
    # Using the email of the first user associated with the tenant (the one who registered it)
    try:
        tenant_user = User.objects.filter(tenant_id=tenant_id).order_by('date_joined').first()
        tenant_email = tenant_user.email if tenant_user else None
    except Exception as e:
        print(f"Error retrieving tenant contact email: {e}")
        tenant_email = None
    
    # Fallback to default email if no tenant user found
    if not tenant_email:
        tenant_email = getattr(settings, 'DEFAULT_TENANT_NOTIFICATION_EMAIL', 'admin@example.com')
        print(f"Using fallback email for tenant {tenant_id}: {tenant_email}")

    subject = "Notificación de límite de uso excedido - Acción requerida"
    message = (
        f"Hola {tenant.name},\n\n"
        f"Te escribimos para informarte que has alcanzado tu límite de uso para el servicio de {event.limit_type}.\n"
        f"Uso actual: {event.current_usage}, Límite: {event.limit}.\n\n"
        f"Para continuar disfrutando del servicio sin interrupciones, te recomendamos actualizar a nuestro plan premium.\n"
        f"Por favor contacta a ventas@anuarbarrera.dev para más información sobre nuestras opciones premium.\n\n"
        f"Saludos cordiales,\nTu Equipo de Soporte"
    )
    
    notification_service.send_notification(
        tenant_id=str(tenant_id), 
        message=message, 
        recipient_email=tenant_email, 
        subject=subject
    )
    print(f"Usage limit notification sent to tenant {tenant_id} at {tenant_email}.")

from django.core.cache import cache
from core.tenant_management.domain.entities import ErrorInteraction
import logging

logger = logging.getLogger(__name__)

def handle_gemini_api_rate_limit_exceeded(
    event: GeminiApiRateLimitExceeded,
    error_repo: DjangoErrorInteractionRepository = None,
    tenant_repo: DjangoTenantRepository = None
):
    """
    Handler for the GeminiApiRateLimitExceeded event.
    This implements a circuit breaker pattern for tenants hitting the API rate limit.
    """
    # Use provided repositories or create new instances (Dependency Injection)
    error_repo = error_repo or DjangoErrorInteractionRepository()
    tenant_repo = tenant_repo or DjangoTenantRepository()

    tenant_id = event.tenant_id
    error_details = event.error_details
    
    # Define a cache key for the circuit breaker
    cache_key = f"tenant:{tenant_id}:gemini_rate_limited"
    
    # Check if the circuit is already open to avoid sending multiple notifications
    if cache.get(cache_key):
        logger.warning(f"Gemini rate limit circuit for tenant {tenant_id} is already open. Skipping.")
        return

    logger.warning(f"Opening Gemini rate limit circuit for tenant {tenant_id} for 15 minutes.")
    # Open the circuit by setting the flag in the cache for 15 minutes (900 seconds)
    cache.set(cache_key, True, timeout=900)

    # 1. Log the error to the database
    try:
        error_interaction = ErrorInteraction(
            tenant_id=tenant_id,
            error_type="GeminiApiRateLimitExceeded",
            details={"reason": error_details}
        )
        error_repo.save(error_interaction)
        logger.info(f"Logged GeminiApiRateLimitExceeded event to DB for tenant {tenant_id}.")
    except Exception as e:
        logger.error(f"Failed to log GeminiApiRateLimitExceeded to DB for tenant {tenant_id}: {e}")

    # 2. Send a notification to the tenant
    try:
        tenant = tenant_repo.get_by_id(tenant_id)
        if not tenant:
            logger.error(f"Error: Tenant {tenant_id} not found when handling Gemini API rate limit exceeded event.")
            return

        # Get the tenant's contact email from the first associated user
        try:
            tenant_user = User.objects.filter(tenant_id=tenant_id).order_by('date_joined').first()
            tenant_email = tenant_user.email if tenant_user else None
        except Exception as e:
            logger.error(f"Error retrieving tenant contact email for {tenant_id}: {e}")
            tenant_email = None

        if not tenant_email:
            logger.error(f"Tenant {tenant_id} does not have a contact email. Cannot send rate limit notification.")
            return

        subject = "Acción requerida: Procesamiento de IA pausado debido al límite de API"
        body = f"""Estimado/a {tenant.name},

Te escribimos para informarte que tu cuenta ha excedido los límites de tasa de la API de Gemini AI.

Como resultado, todo el análisis y generación de respuestas con IA para tu cuenta se han pausado temporalmente durante 15 minutos para permitir que la cuota de API se restablezca.

Esto suele ocurrir al usar el nivel gratuito de la API de Gemini, que tiene un límite de 15 solicitudes por minuto. Para garantizar un servicio ininterrumpido, por favor considera actualizar tu proyecto de Google Cloud a un plan de pago, que proporciona límites significativamente más altos.

No necesitas hacer nada en este momento; el sistema reanudará automáticamente el procesamiento después del período de enfriamiento. Sin embargo, si este problema persiste, actualizar tu plan de API de Gemini es la solución recomendada.

Gracias,
Tu Equipo de Soporte"""
        
        notification_service.send_notification(
            tenant_id=str(tenant_id),
            message=body,
            recipient_email=tenant_email,
            subject=subject
        )
        logger.info(f"Gemini API rate limit notification sent to tenant {tenant_id} at {tenant_email}.")
    except Exception as e:
        logger.error(f"Failed to send Gemini API rate limit notification to tenant {tenant_id}: {e}", exc_info=True)

def handle_plan_changed(event: PlanChanged):
    """
    Handler for the PlanChanged event.
    Sends a notification to the tenant when their plan is upgraded to premium.
    """
    tenant_id = event.tenant_id
    
    # We only care about upgrades to the premium plan
    if event.new_plan_name.lower() != PlanName.PREMIUM.value.lower():
        return

    try:
        tenant_repo = DjangoTenantRepository()
        tenant = tenant_repo.find_by_id(tenant_id)
        if not tenant:
            logger.error(f"Error: Tenant {tenant_id} not found when handling plan changed event.")
            return
        
        tenant_email = getattr(tenant, 'contact_email', None)
        if not tenant_email:
            logger.error(f"Tenant {tenant_id} does not have a contact email. Cannot send plan change notification.")
            return

        subject = "¡Bienvenido al Plan Premium!"
        body = f"""¡Felicitaciones! Tu plan ha sido actualizado exitosamente a Premium.

Ahora tienes acceso a todas nuestras funciones premium, incluidas interacciones ilimitadas y soporte prioritario.

Gracias por ser un cliente valioso."""
        
        send_notification_to_tenant(tenant_id, subject, body)
        logger.info(f"Premium plan upgrade notification sent to tenant {tenant_id} at {tenant_email}.")
    except Exception as e:
        logger.error(f"Failed to send plan change notification to tenant {tenant_id}: {e}", exc_info=True)


# Register all handlers
register_handler(UsageLimitExceeded, handle_usage_limit_exceeded)
register_handler(GeminiApiRateLimitExceeded, handle_gemini_api_rate_limit_exceeded)
register_handler(PlanChanged, handle_plan_changed)


def handle_plan_changed(event: PlanChanged):
    """
    Handler for the PlanChanged event.
    Sends a notification to the tenant when their plan is upgraded to premium.
    """
    # We only care about upgrades to the premium plan
    if event.new_plan_name.lower() != PlanName.PREMIUM.value.lower():
        return

    tenant_id = event.tenant_id
    tenant = tenant_repository.get_by_id(tenant_id)
    if not tenant:
        print(f"Error: Tenant {tenant_id} not found when handling plan changed event.")
        return

    try:
        tenant_user = User.objects.filter(tenant_id=tenant_id).order_by('date_joined').first()
        tenant_email = tenant_user.email if tenant_user else None
    except Exception as e:
        print(f"Error retrieving tenant contact email: {e}")
        tenant_email = None

    if not tenant_email:
        tenant_email = getattr(settings, 'DEFAULT_TENANT_NOTIFICATION_EMAIL', 'admin@example.com')
        print(f"Using fallback email for tenant {tenant_id}: {tenant_email}")

    subject = "¡Bienvenido al Plan Premium!"
    message = (
        f"Hola {tenant.name},\n\n"
        f"¡Felicitaciones! Tu plan ha sido actualizado exitosamente a Premium.\n\n"
        f"Ahora tienes acceso a todas nuestras funciones premium, incluidas interacciones ilimitadas y soporte prioritario.\n\n"
        f"Estamos emocionados de tenerte a bordo.\n\n"
        f"Saludos cordiales,\nTu Equipo de Soporte"
    )
    
    notification_service.send_notification(
        tenant_id=str(tenant_id), 
        message=message, 
        recipient_email=tenant_email, 
        subject=subject
    )
    print(f"Premium plan upgrade notification sent to tenant {tenant_id} at {tenant_email}.")

# Registrar el manejador de eventos
register_handler(UsageLimitExceeded, handle_usage_limit_exceeded)
register_handler(GeminiApiRateLimitExceeded, handle_gemini_api_rate_limit_exceeded)
register_handler(PlanChanged, handle_plan_changed)
