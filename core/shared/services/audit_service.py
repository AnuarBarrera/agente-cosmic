"""
Audit Service for DIALOGIX
Centralized service for logging all system activities and changes
"""

import json
from typing import Dict, Any, Optional, List, Union
from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.http import HttpRequest
from django.db import transaction
from django.utils import timezone
from django.conf import settings
from ..models.audit_trail import AuditEvent, SecurityEvent, DataAccessLog, SystemChangeLog
import logging

User = get_user_model()
logger = logging.getLogger('core.audit')

class AuditService:
    """
    Service for creating and managing audit logs
    """
    
    @classmethod
    def log_model_change(
        cls,
        instance,
        action: str,
        user: Optional[User] = None,
        request: Optional[HttpRequest] = None,
        old_values: Optional[Dict] = None,
        new_values: Optional[Dict] = None,
        changed_fields: Optional[List[str]] = None,
        category: str = None,
        description: str = None
    ) -> AuditEvent:
        """
        Log model instance changes (create, update, delete)
        """
        try:
            # Get content type for the instance
            content_type = ContentType.objects.get_for_model(instance)
            
            # Generate description if not provided
            if not description:
                model_name = content_type.model
                description = f"{action.title()} {model_name} (ID: {instance.pk})"
            
            # Get category from model if not provided
            if not category:
                category = content_type.app_label
            
            # Get tenant information
            tenant = None
            if hasattr(instance, 'tenant'):
                tenant = instance.tenant
            elif user and hasattr(user, 'tenant'):
                tenant = user.tenant
            
            # Create audit event
            audit_event = AuditEvent.objects.create(
                event_type=action.upper(),
                description=description,
                category=category,
                user=user,
                tenant=tenant,
                content_type=content_type,
                object_id=str(instance.pk),
                old_values=old_values,
                new_values=new_values,
                changed_fields=changed_fields,
                **cls._extract_request_info(request)
            )
            
            logger.info(f"Audit event created: {audit_event.id}")
            return audit_event
            
        except Exception as e:
            logger.error(f"Failed to create audit event: {e}")
            # Don't fail the main operation if audit logging fails
            return None
    
    @classmethod
    def log_user_action(
        cls,
        action: str,
        user: User,
        description: str,
        request: Optional[HttpRequest] = None,
        category: str = 'user_action',
        severity: str = 'LOW',
        metadata: Optional[Dict] = None
    ) -> AuditEvent:
        """
        Log user actions (login, logout, permission changes, etc.)
        """
        try:
            # Get tenant
            tenant = getattr(user, 'tenant', None)
            
            audit_event = AuditEvent.objects.create(
                event_type=action.upper(),
                description=description,
                category=category,
                severity=severity,
                user=user,
                tenant=tenant,
                metadata=metadata or {},
                **cls._extract_request_info(request)
            )
            
            logger.info(f"User action logged: {action} by {user}")
            return audit_event
            
        except Exception as e:
            logger.error(f"Failed to log user action: {e}")
            return None
    
    @classmethod
    def log_security_event(
        cls,
        event_type: str,
        description: str,
        user: Optional[User] = None,
        request: Optional[HttpRequest] = None,
        severity: str = 'HIGH',
        risk_score: int = 50,
        attack_vector: str = None,
        affected_resources: List[str] = None,
        response_required: bool = True
    ) -> SecurityEvent:
        """
        Log security events that require attention
        """
        try:
            # Get tenant
            tenant = None
            if user and hasattr(user, 'tenant'):
                tenant = user.tenant
            
            # Create base audit event
            audit_event = AuditEvent.objects.create(
                event_type='SECURITY_EVENT',
                description=description,
                category='security',
                severity=severity,
                user=user,
                tenant=tenant,
                is_security_event=True,
                requires_attention=response_required,
                **cls._extract_request_info(request)
            )
            
            # Create specialized security event
            security_event = SecurityEvent.objects.create(
                audit_event=audit_event,
                event_type=event_type,
                risk_score=risk_score,
                impact_level=severity,
                attack_vector=attack_vector,
                affected_resources=affected_resources or [],
                response_required=response_required,
                response_deadline=cls._calculate_response_deadline(severity)
            )
            
            logger.warning(f"Security event logged: {event_type} - Risk: {risk_score}")
            
            # Send alerts for high-risk events
            if risk_score >= 80 or severity in ['HIGH', 'CRITICAL']:
                cls._send_security_alert(security_event)
            
            return security_event
            
        except Exception as e:
            logger.error(f"Failed to log security event: {e}")
            return None
    
    @classmethod
    def log_data_access(
        cls,
        accessor: User,
        data_subject: User,
        access_type: str,
        data_category: str,
        purpose: str,
        legal_basis: str,
        request: Optional[HttpRequest] = None,
        fields_accessed: List[str] = None,
        records_count: int = 1
    ) -> DataAccessLog:
        """
        Log data access events for GDPR compliance
        """
        try:
            access_log = DataAccessLog.objects.create(
                accessor=accessor,
                data_subject=data_subject,
                access_type=access_type,
                data_category=data_category,
                fields_accessed=fields_accessed or [],
                legal_basis=legal_basis,
                purpose=purpose,
                request_source=cls._get_request_source(request),
                records_accessed=records_count,
                **cls._extract_request_info(request, include_user_agent=True)
            )
            
            logger.info(f"Data access logged: {accessor} accessed {data_subject}'s {data_category}")
            return access_log
            
        except Exception as e:
            logger.error(f"Failed to log data access: {e}")
            return None
    
    @classmethod
    def log_system_change(
        cls,
        change_type: str,
        component: str,
        description: str,
        changed_by: Optional[User] = None,
        change_reason: str = None,
        version_before: str = None,
        version_after: str = None,
        configuration_diff: Optional[Dict] = None,
        affected_services: List[str] = None,
        downtime_minutes: int = 0,
        users_affected: int = 0
    ) -> SystemChangeLog:
        """
        Log system configuration and infrastructure changes
        """
        try:
            change_log = SystemChangeLog.objects.create(
                change_type=change_type,
                component=component,
                description=description,
                changed_by=changed_by,
                change_reason=change_reason or "Not specified",
                version_before=version_before,
                version_after=version_after,
                configuration_diff=configuration_diff,
                affected_services=affected_services or [],
                downtime_minutes=downtime_minutes,
                users_affected=users_affected
            )
            
            logger.info(f"System change logged: {change_type} on {component}")
            return change_log
            
        except Exception as e:
            logger.error(f"Failed to log system change: {e}")
            return None
    
    @classmethod
    def log_api_call(
        cls,
        request: HttpRequest,
        response_status: int,
        user: Optional[User] = None,
        endpoint: str = None,
        processing_time: float = None,
        error_details: str = None
    ) -> AuditEvent:
        """
        Log API calls for monitoring and security
        """
        try:
            # Get tenant
            tenant = None
            if user and hasattr(user, 'tenant'):
                tenant = user.tenant
            
            # Prepare metadata
            metadata = {
                'response_status': response_status,
                'processing_time': processing_time,
                'endpoint': endpoint or request.path,
            }
            
            if error_details:
                metadata['error_details'] = error_details
            
            # Determine severity based on status code
            if response_status >= 500:
                severity = 'HIGH'
            elif response_status >= 400:
                severity = 'MEDIUM'
            else:
                severity = 'LOW'
            
            audit_event = AuditEvent.objects.create(
                event_type='API_CALL',
                description=f"{request.method} {endpoint or request.path}",
                category='api',
                severity=severity,
                user=user,
                tenant=tenant,
                metadata=metadata,
                **cls._extract_request_info(request)
            )
            
            return audit_event
            
        except Exception as e:
            logger.error(f"Failed to log API call: {e}")
            return None
    
    @classmethod
    def _extract_request_info(cls, request: Optional[HttpRequest], include_user_agent: bool = False) -> Dict:
        """
        Extract common request information for audit logs
        """
        if not request:
            return {}
        
        info = {
            'ip_address': cls._get_client_ip(request),
            'request_method': request.method,
            'request_path': request.path,
        }
        
        if include_user_agent:
            info['user_agent'] = request.META.get('HTTP_USER_AGENT', '')[:500]  # Limit length
        
        # Get session ID if available
        if hasattr(request, 'session') and request.session.session_key:
            info['session_id'] = request.session.session_key
        
        return info
    
    @classmethod
    def _get_client_ip(cls, request: HttpRequest) -> str:
        """
        Get client IP address from request
        """
        x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
        if x_forwarded_for:
            return x_forwarded_for.split(',')[0].strip()
        return request.META.get('REMOTE_ADDR', 'unknown')
    
    @classmethod
    def _get_request_source(cls, request: Optional[HttpRequest]) -> str:
        """
        Determine the source of the request (API, web, admin, etc.)
        """
        if not request:
            return 'unknown'
        
        path = request.path
        if path.startswith('/api/'):
            return 'api'
        elif path.startswith('/admin/'):
            return 'admin'
        else:
            return 'web'
    
    @classmethod
    def _calculate_response_deadline(cls, severity: str):
        """
        Calculate response deadline based on severity
        """
        now = timezone.now()
        
        deadlines = {
            'LOW': 72,      # 72 hours
            'MEDIUM': 24,   # 24 hours
            'HIGH': 4,      # 4 hours
            'CRITICAL': 1,  # 1 hour
        }
        
        hours = deadlines.get(severity, 24)
        return now + timezone.timedelta(hours=hours)
    
    @classmethod
    def _send_security_alert(cls, security_event: SecurityEvent):
        """
        Send alerts for high-priority security events
        """
        # In production, this would integrate with alerting systems
        # like Slack, PagerDuty, email, etc.
        logger.critical(
            f"SECURITY ALERT: {security_event.event_type} - "
            f"Risk Score: {security_event.risk_score} - "
            f"ID: {security_event.id}"
        )
    
    @classmethod
    def get_audit_summary(cls, tenant_id: str = None, days: int = 30) -> Dict[str, Any]:
        """
        Get audit activity summary for monitoring dashboard
        """
        try:
            from django.db.models import Count
            from datetime import timedelta
            
            cutoff_date = timezone.now() - timedelta(days=days)
            
            # Base queryset
            queryset = AuditEvent.objects.filter(timestamp__gte=cutoff_date)
            if tenant_id:
                queryset = queryset.filter(tenant_id=tenant_id)
            
            # Get counts by event type
            event_counts = queryset.values('event_type').annotate(
                count=Count('id')
            ).order_by('-count')
            
            # Get security events
            security_events = SecurityEvent.objects.filter(
                audit_event__timestamp__gte=cutoff_date,
                status__in=['OPEN', 'INVESTIGATING']
            )
            
            if tenant_id:
                security_events = security_events.filter(
                    audit_event__tenant_id=tenant_id
                )
            
            return {
                'total_events': queryset.count(),
                'event_types': list(event_counts),
                'security_events_open': security_events.count(),
                'high_risk_events': security_events.filter(risk_score__gte=80).count(),
                'period_days': days,
                'generated_at': timezone.now().isoformat(),
            }
            
        except Exception as e:
            logger.error(f"Failed to generate audit summary: {e}")
            return {}


# Create singleton instance
audit_service = AuditService()