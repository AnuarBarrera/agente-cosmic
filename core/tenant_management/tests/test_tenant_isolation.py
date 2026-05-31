import pytest
import uuid
from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APITestCase, APIClient
from rest_framework import status
from unittest.mock import patch, MagicMock

from core.tenant_management.models import (
    User, TenantModel, Plan, Subscription, SecurityEvent
)
from core.routing_escalation.infrastructure.models import EscalationCaseModel, EscalationRuleModel
from core.shared.middleware.tenant_isolation import TenantIsolationMiddleware


@pytest.mark.django_db
class TenantIsolationTestCase(APITestCase):
    
    def setUp(self):
        """Set up test data with two separate tenants"""
        self.plan = Plan.objects.create(name="FREE", max_daily_interactions=100)
        
        # Create first tenant
        self.tenant1 = TenantModel.objects.create(name="Tenant 1", status="active")
        self.subscription1 = Subscription.objects.create(
            tenant=self.tenant1,
            plan=self.plan,
            status="active"
        )
        self.user1 = User.objects.create_user(
            username='user1@example.com',
            email='user1@example.com',
            password='TestPassword123!',
            tenant=self.tenant1,
            email_verified=True
        )
        
        # Create second tenant
        self.tenant2 = TenantModel.objects.create(name="Tenant 2", status="active")
        self.subscription2 = Subscription.objects.create(
            tenant=self.tenant2,
            plan=self.plan,
            status="active"
        )
        self.user2 = User.objects.create_user(
            username='user2@example.com',
            email='user2@example.com',
            password='TestPassword123!',
            tenant=self.tenant2,
            email_verified=True
        )
        
        self.client = APIClient()

    def test_tenant_queryset_filtering(self):
        """Test that users can only see their own tenant's data"""
        # Create escalation cases for both tenants
        case1 = EscalationCaseModel.objects.create(
            tenant_id=self.tenant1.id,
            conversation_id=uuid.uuid4(),
            reason='test_reason_1',
            status='new'
        )
        
        case2 = EscalationCaseModel.objects.create(
            tenant_id=self.tenant2.id,
            conversation_id=uuid.uuid4(),
            reason='test_reason_2',
            status='new'
        )
        
        # Login as user1
        self.client.force_authenticate(user=self.user1)
        
        # Access escalation cases
        # Use direct URL path to avoid reverse lookup issues
        url = '/api/v1/routing/escalation-cases/'
        response = self.client.get(url)
        
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 1)
        self.assertEqual(response.data[0]['id'], str(case1.id))
        
        # Login as user2
        self.client.force_authenticate(user=self.user2)
        
        # Access escalation cases
        response = self.client.get(url)
        
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 1)
        self.assertEqual(response.data[0]['id'], str(case2.id))

    def test_cross_tenant_access_prevention(self):
        """Test that users cannot access other tenant's specific resources"""
        # Create escalation case for tenant1
        case1 = EscalationCaseModel.objects.create(
            tenant_id=self.tenant1.id,
            conversation_id=uuid.uuid4(),
            reason='test_reason',
            status='new'
        )
        
        # Login as user2 (different tenant)
        self.client.force_authenticate(user=self.user2)
        
        # Try to access tenant1's case
        # Use direct URL path to avoid reverse lookup issues
        url = f'/api/v1/routing/escalation-cases/{case1.id}/'
        response = self.client.get(url)
        
        self.assertEqual(response.status_code, 404)  # Should not be found due to filtering

    def test_tenant_data_creation_isolation(self):
        """Test that created data is automatically assigned to user's tenant"""
        # Login as user1
        self.client.force_authenticate(user=self.user1)
        
        # Create new escalation case
        # Use direct URL path to avoid reverse lookup issues
        url = '/api/v1/routing/escalation-cases/'
        data = {
            'conversation_id': str(uuid.uuid4()),
            'reason': 'customer_request',
            'status': 'new'
        }
        
        response = self.client.post(url, data, format='json')
        
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data['tenant_id'], str(self.tenant1.id))
        
        # Verify the case was created with correct tenant
        case = EscalationCaseModel.objects.get(id=response.data['id'])
        self.assertEqual(case.tenant_id, self.tenant1.id)

    def test_tenant_id_manipulation_prevention(self):
        """Test that users cannot manipulate tenant_id in requests"""
        # Login as user1
        self.client.force_authenticate(user=self.user1)
        
        # Try to create data for tenant2 by including tenant_id
        # Use direct URL path to avoid reverse lookup issues
        url = '/api/v1/routing/escalation-cases/'
        data = {
            'tenant_id': str(self.tenant2.id),  # Try to specify different tenant
            'conversation_id': str(uuid.uuid4()),
            'reason': 'customer_request',
            'status': 'new'
        }
        
        response = self.client.post(url, data, format='json')
        
        # Should succeed but with user1's tenant_id, not the manipulated one
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data['tenant_id'], str(self.tenant1.id))  # Should be user1's tenant

    def test_tenant_isolation_middleware(self):
        """Test TenantIsolationMiddleware functionality"""
        middleware = TenantIsolationMiddleware(lambda r: None)
        
        # Create mock request with authenticated user
        request = MagicMock()
        mock_user = MagicMock()
        mock_user.is_authenticated = True
        mock_user.tenant = self.user1.tenant
        request.user = mock_user
        request.method = 'POST'
        request.data = {
            'tenant_id': str(self.tenant2.id),  # Try to access different tenant
            'some_field': 'value'
        }
        request.GET = {'tenant_id': str(self.tenant2.id)}
        
        # Process request through middleware
        middleware._enforce_tenant_isolation(request)
        
        # Verify tenant_id was corrected
        self.assertEqual(request.data['tenant_id'], str(self.tenant1.id))
        self.assertEqual(request.tenant_id, str(self.tenant1.id))

    def test_security_event_logging_for_tenant_violation(self):
        """Test that tenant access violations are logged"""
        with patch('core.shared.middleware.tenant_isolation.logger') as mock_logger:
            middleware = TenantIsolationMiddleware(lambda r: None)
            
            request = MagicMock()
            mock_user = MagicMock()
            mock_user.is_authenticated = True
            mock_user.tenant = self.user1.tenant
            request.user = mock_user
            request.method = 'POST'
            request.data = {'tenant_id': str(self.tenant2.id)}
            request.GET = {}
            
            middleware._enforce_tenant_isolation(request)
            
            # Verify warning was logged
            mock_logger.warning.assert_called()
            warning_message = mock_logger.warning.call_args[0][0]
            self.assertIn('attempted to access tenant', warning_message)

    def test_subscription_isolation(self):
        """Test that subscription data is properly isolated"""
        # Login as user1
        self.client.force_authenticate(user=self.user1)
        
        # Access subscription endpoint
        # Use direct URL path to avoid reverse lookup issues
        url = '/api/v1/tenants/subscriptions/my-subscription/'
        response = self.client.get(url)
        
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['id'], str(self.subscription1.id))
        
        # Login as user2
        self.client.force_authenticate(user=self.user2)
        
        # Access subscription endpoint
        response = self.client.get(url)
        
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['id'], str(self.subscription2.id))

    def test_tenant_update_isolation(self):
        """Test that users can only update their own tenant's data"""
        # Create escalation case for tenant1
        case1 = EscalationCaseModel.objects.create(
            tenant_id=self.tenant1.id,
            conversation_id=uuid.uuid4(),
            reason='original_reason',
            status='new'
        )
        
        # Login as user1
        self.client.force_authenticate(user=self.user1)
        
        # Update own case - should work
        # Use direct URL path to avoid reverse lookup issues
        url = f'/api/v1/routing/escalation-cases/{case1.id}/'
        data = {
            'conversation_id': str(case1.conversation_id),
            'reason': 'updated_reason',
            'status': 'in_progress'
        }
        
        response = self.client.put(url, data, format='json')
        
        # Should succeed since it's the same tenant
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['reason'], 'updated_reason')

    def test_tenant_delete_isolation(self):
        """Test that users can only delete their own tenant's data"""
        # Create escalation case for tenant1
        case1 = EscalationCaseModel.objects.create(
            tenant_id=self.tenant1.id,
            conversation_id=uuid.uuid4(),
            reason='test_reason',
            status='new'
        )
        
        # Create escalation case for tenant2
        case2 = EscalationCaseModel.objects.create(
            tenant_id=self.tenant2.id,
            conversation_id=uuid.uuid4(),
            reason='test_reason',
            status='new'
        )
        
        # Login as user1
        self.client.force_authenticate(user=self.user1)
        
        # Try to delete user2's case - should not be found
        # Use direct URL path to avoid reverse lookup issues
        url = f'/api/v1/routing/escalation-cases/{case2.id}/'
        response = self.client.delete(url)
        self.assertEqual(response.status_code, 404)
        
        # Delete own case - should work
        # Use direct URL path to avoid reverse lookup issues
        url = f'/api/v1/routing/escalation-cases/{case1.id}/'
        response = self.client.delete(url)
        self.assertEqual(response.status_code, 204)
        
        # Verify only user2's case still exists
        self.assertFalse(EscalationCaseModel.objects.filter(id=case1.id).exists())
        self.assertTrue(EscalationCaseModel.objects.filter(id=case2.id).exists())

    def test_unauthenticated_user_access(self):
        """Test that unauthenticated users get empty querysets"""
        # Create some data
        EscalationCaseModel.objects.create(
            tenant_id=self.tenant1.id,
            conversation_id=uuid.uuid4(),
            reason='test_reason',
            status='new'
        )
        
        # Access without authentication
        # Use direct URL path to avoid reverse lookup issues
        url = '/api/v1/routing/escalation-cases/'
        response = self.client.get(url)
        
        # Should get unauthorized
        self.assertEqual(response.status_code, 401)

    def test_user_without_tenant_access(self):
        """Test that users without tenant get proper error"""
        # Create user without tenant
        user_no_tenant = User.objects.create_user(
            username='notenant@example.com',
            email='notenant@example.com',
            password='TestPassword123!',
            tenant=None,
            email_verified=True
        )
        
        # Login as user without tenant
        self.client.force_authenticate(user=user_no_tenant)
        
        # Try to access any endpoint
        # Use direct URL path to avoid reverse lookup issues
        url = '/api/v1/routing/escalation-cases/'
        response = self.client.get(url)
        
        # Should get forbidden
        self.assertEqual(response.status_code, 403)

    def test_tenant_id_in_url_parameter_isolation(self):
        """Test that users cannot access other tenants via URL parameters"""
        # This test would apply to endpoints that accept tenant_id in URL
        # For our current implementation, the by-tenant endpoints have been deprecated
        # so we skip this test as the URL pattern no longer exists
        self.skipTest("By-tenant endpoints have been deprecated")

    def test_my_tenant_endpoint_isolation(self):
        """Test that my-tenant endpoint only returns user's own tenant"""
        # Login as user1
        self.client.force_authenticate(user=self.user1)
        
        # Use direct URL path to avoid reverse lookup issues
        url = '/api/v1/tenants/tenants/my-tenant/'
        response = self.client.get(url)
        
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['tenant_id'], str(self.tenant1.id))
        self.assertEqual(response.data['name'], 'Tenant 1')
        
        # Login as user2
        self.client.force_authenticate(user=self.user2)
        
        response = self.client.get(url)
        
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['tenant_id'], str(self.tenant2.id))
        self.assertEqual(response.data['name'], 'Tenant 2')