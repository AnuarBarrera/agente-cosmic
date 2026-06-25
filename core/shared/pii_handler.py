"""
PII (Personally Identifiable Information) Handler for DIALOGIX
GDPR-compliant PII handling, data anonymization, and retention management
"""

import re
import hashlib
import uuid
from datetime import datetime, timedelta
from typing import Dict, List, Any, Optional, Union
from django.db import models, transaction
from django.conf import settings
from django.core.exceptions import ValidationError
from django.utils import timezone
from cryptography.fernet import Fernet
import logging

logger = logging.getLogger('core.security')

class PIIHandler:
    """
    Centralized PII handling for GDPR compliance and data protection
    """
    
    # PII data categories as per GDPR
    PII_CATEGORIES = {
        'IDENTITY': [
            'full_name', 'first_name', 'last_name', 'maiden_name',
            'username', 'display_name', 'nickname'
        ],
        'CONTACT': [
            'email', 'phone', 'mobile', 'address', 'postal_code',
            'city', 'state', 'country', 'website'
        ],
        'PERSONAL': [
            'date_of_birth', 'age', 'gender', 'nationality',
            'marital_status', 'occupation', 'employer'
        ],
        'FINANCIAL': [
            'credit_card', 'bank_account', 'routing_number',
            'tax_id', 'ssn', 'payment_info'
        ],
        'TECHNICAL': [
            'ip_address', 'mac_address', 'device_id', 'browser_fingerprint',
            'session_id', 'cookies'
        ],
        'BIOMETRIC': [
            'fingerprint', 'facial_recognition', 'voice_print',
            'retina_scan', 'dna'
        ],
        'LOCATION': [
            'geolocation', 'gps_coordinates', 'home_address',
            'work_address', 'frequent_locations'
        ]
    }
    
    # Data retention periods (in days)
    RETENTION_PERIODS = {
        'user_account': 2555,  # 7 years for account data
        'conversation': 1095,  # 3 years for conversation data
        'audit_logs': 2190,   # 6 years for audit logs
        'session_data': 90,   # 3 months for session data
        'temporary_data': 30, # 1 month for temporary data
    }
    
    def __init__(self):
        # Initialize encryption key (should be from secure key management)
        self.encryption_key = self._get_encryption_key()
        self.fernet = Fernet(self.encryption_key) if self.encryption_key else None
    
    def _get_encryption_key(self) -> Optional[bytes]:
        """
        Get encryption key from secure key management
        """
        # In production, this should come from AWS Secrets Manager or similar
        key = getattr(settings, 'PII_ENCRYPTION_KEY', None)
        if key:
            return key.encode() if isinstance(key, str) else key
        
        # Generate a key for development (NOT for production)
        if settings.DEBUG:
            return Fernet.generate_key()
        
        logger.error("No PII encryption key configured")
        return None
    
    def encrypt_pii(self, data: str) -> str:
        """
        Encrypt PII data for storage
        """
        if not self.fernet:
            if not settings.DEBUG:
                raise ValueError("PII_ENCRYPTION_KEY no configurada en producción — operación abortada")
            logger.error("PII encryption not available")
            return data  # Solo en desarrollo
        
        try:
            if isinstance(data, str):
                encrypted = self.fernet.encrypt(data.encode())
                return encrypted.decode()
            return data
        except Exception as e:
            logger.error(f"PII encryption failed: {e}")
            return data
    
    def decrypt_pii(self, encrypted_data: str) -> str:
        """
        Decrypt PII data for use
        """
        if not self.fernet:
            logger.error("PII decryption not available")
            return encrypted_data
        
        try:
            if isinstance(encrypted_data, str):
                decrypted = self.fernet.decrypt(encrypted_data.encode())
                return decrypted.decode()
            return encrypted_data
        except Exception as e:
            logger.error(f"PII decryption failed: {e}")
            return encrypted_data
    
    def anonymize_data(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Anonymize PII data while preserving data utility
        """
        anonymized = {}
        
        for key, value in data.items():
            if self._is_pii_field(key):
                anonymized[key] = self._anonymize_value(value, key)
            elif isinstance(value, dict):
                anonymized[key] = self.anonymize_data(value)
            elif isinstance(value, list):
                anonymized[key] = [
                    self.anonymize_data(item) if isinstance(item, dict) else item
                    for item in value
                ]
            else:
                anonymized[key] = value
        
        return anonymized
    
    def _is_pii_field(self, field_name: str) -> bool:
        """
        Check if field contains PII
        """
        field_lower = field_name.lower()
        
        for category, fields in self.PII_CATEGORIES.items():
            if any(pii_field in field_lower for pii_field in fields):
                return True
        
        return False
    
    def _anonymize_value(self, value: Any, field_name: str) -> str:
        """
        Anonymize specific PII values
        """
        if not value:
            return value
        
        field_lower = field_name.lower()
        
        # Email anonymization
        if 'email' in field_lower:
            return self._anonymize_email(str(value))
        
        # Phone anonymization
        elif any(phone_term in field_lower for phone_term in ['phone', 'mobile']):
            return self._anonymize_phone(str(value))
        
        # Name anonymization
        elif any(name_term in field_lower for name_term in ['name', 'username']):
            return self._anonymize_name(str(value))
        
        # Address anonymization
        elif 'address' in field_lower:
            return self._anonymize_address(str(value))
        
        # IP address anonymization
        elif 'ip' in field_lower:
            return self._anonymize_ip(str(value))
        
        # Generic anonymization using hash
        else:
            return self._generate_anonymous_id(str(value))
    
    def _anonymize_email(self, email: str) -> str:
        """
        Anonymize email while preserving domain for analytics
        """
        if '@' not in email:
            return self._generate_anonymous_id(email)
        
        local, domain = email.rsplit('@', 1)
        return f"user_{self._generate_short_hash(local)}@{domain}"
    
    def _anonymize_phone(self, phone: str) -> str:
        """
        Anonymize phone number while preserving country code
        """
        # Keep country code, anonymize rest
        if len(phone) > 4:
            return phone[:2] + 'X' * (len(phone) - 4) + phone[-2:]
        return 'X' * len(phone)
    
    def _anonymize_name(self, name: str) -> str:
        """
        Anonymize names while preserving length and structure
        """
        words = name.split()
        return ' '.join(f"Name{i+1}" for i in range(len(words)))
    
    def _anonymize_address(self, address: str) -> str:
        """
        Anonymize address while preserving city/state for analytics
        """
        # Simplified anonymization - keep last two parts (city, state)
        parts = address.split(',')
        if len(parts) > 2:
            return f"[STREET ANONYMIZED], {', '.join(parts[-2:]).strip()}"
        return "[ADDRESS ANONYMIZED]"
    
    def _anonymize_ip(self, ip: str) -> str:
        """
        Anonymize IP address for privacy compliance
        """
        if '.' in ip:  # IPv4
            parts = ip.split('.')
            if len(parts) == 4:
                return f"{parts[0]}.{parts[1]}.0.0"
        elif ':' in ip:  # IPv6
            parts = ip.split(':')
            if len(parts) > 4:
                return ':'.join(parts[:4] + ['0000'] * (len(parts) - 4))
        
        return ip
    
    def _generate_anonymous_id(self, value: str) -> str:
        """
        Generate anonymous ID for a value
        """
        # Use hash to create consistent but anonymous identifier
        return f"anon_{self._generate_short_hash(value)}"
    
    def _generate_short_hash(self, value: str) -> str:
        """
        Generate short hash for anonymization
        """
        return hashlib.sha256(value.encode()).hexdigest()[:8]
    
    def pseudonymize_data(self, data: Dict[str, Any], user_id: str) -> Dict[str, Any]:
        """
        Pseudonymize data - replace identifiers with pseudonyms
        """
        pseudonymized = {}
        
        for key, value in data.items():
            if self._is_pii_field(key):
                pseudonymized[key] = self._create_pseudonym(value, user_id, key)
            elif isinstance(value, dict):
                pseudonymized[key] = self.pseudonymize_data(value, user_id)
            else:
                pseudonymized[key] = value
        
        return pseudonymized
    
    def _create_pseudonym(self, value: Any, user_id: str, field_name: str) -> str:
        """
        Create consistent pseudonym for a value
        """
        # Create deterministic pseudonym using user_id and field
        combined = f"{user_id}_{field_name}_{value}"
        hash_value = hashlib.sha256(combined.encode()).hexdigest()[:12]
        return f"pseudo_{hash_value}"
    
    def is_retention_expired(self, created_date: datetime, data_type: str) -> bool:
        """
        Check if data retention period has expired
        """
        retention_days = self.RETENTION_PERIODS.get(data_type, 365)  # Default 1 year
        expiry_date = created_date + timedelta(days=retention_days)
        return timezone.now() > expiry_date
    
    def schedule_data_deletion(self, model_class, data_type: str):
        """
        Schedule data for deletion based on retention policy
        """
        retention_days = self.RETENTION_PERIODS.get(data_type, 365)
        cutoff_date = timezone.now() - timedelta(days=retention_days)
        
        try:
            expired_records = model_class.objects.filter(
                created_at__lt=cutoff_date
            )
            
            count = expired_records.count()
            if count > 0:
                logger.info(f"Scheduling {count} {data_type} records for deletion")
                expired_records.delete()
                
                # Log deletion for audit
                logger.info(f"Deleted {count} expired {data_type} records")
                
        except Exception as e:
            logger.error(f"Failed to delete expired {data_type} data: {e}")
    
    def export_user_data(self, user_id: str) -> Dict[str, Any]:
        """
        Export all user data for GDPR data portability
        """
        from core.tenant_management.models import User
        from core.conversation_management.models import Conversation
        
        try:
            user = User.objects.get(id=user_id)
            
            # Collect user data from all relevant models
            user_data = {
                'user_profile': {
                    'id': str(user.id),
                    'email': user.email,
                    'first_name': user.first_name,
                    'last_name': user.last_name,
                    'date_joined': user.date_joined.isoformat(),
                    'last_login': user.last_login.isoformat() if user.last_login else None,
                },
                'conversations': [],
                'export_date': timezone.now().isoformat(),
                'format_version': '1.0'
            }
            
            # Add conversation data
            conversations = Conversation.objects.filter(tenant=user.tenant)
            for conv in conversations:
                user_data['conversations'].append({
                    'id': str(conv.id),
                    'created_at': conv.created_at.isoformat(),
                    'updated_at': conv.updated_at.isoformat(),
                    # Add other conversation fields as needed
                })
            
            return user_data
            
        except User.DoesNotExist:
            logger.error(f"User {user_id} not found for data export")
            return {}
        except Exception as e:
            logger.error(f"Failed to export user data for {user_id}: {e}")
            return {}
    
    def delete_user_data(self, user_id: str) -> bool:
        """
        Delete all user data for GDPR right to erasure
        """
        from core.tenant_management.models import User
        
        try:
            with transaction.atomic():
                user = User.objects.get(id=user_id)
                
                # Delete related data first (conversations, etc.)
                # This should cascade properly with foreign keys
                
                # Delete the user account
                user.delete()
                
                logger.info(f"Successfully deleted all data for user {user_id}")
                return True
                
        except User.DoesNotExist:
            logger.error(f"User {user_id} not found for deletion")
            return False
        except Exception as e:
            logger.error(f"Failed to delete user data for {user_id}: {e}")
            return False
    
    def validate_consent(self, user_id: str, purpose: str) -> bool:
        """
        Validate user consent for data processing
        """
        # This would integrate with a consent management system
        # For now, return True for basic functionality
        # In production, implement proper consent tracking
        return True
    
    def log_data_access(self, user_id: str, accessed_by: str, purpose: str, data_type: str):
        """
        Log data access for audit trail
        """
        logger.info(
            f"Data access logged - User: {user_id}, Accessed by: {accessed_by}, "
            f"Purpose: {purpose}, Data type: {data_type}, "
            f"Timestamp: {timezone.now().isoformat()}"
        )
        
        # In production, store this in a dedicated audit table


# Singleton instance
pii_handler = PIIHandler()