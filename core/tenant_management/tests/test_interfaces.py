import pytest
from django.test import TestCase
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient
from rest_framework import status
from django.urls import reverse
import uuid
from unittest.mock import patch

from core.tenant_management.models import TenantModel, Plan, Subscription, TenantConfigurationModel

User = get_user_model()

class AuthAPITests(TestCase):
    def setUp(self):
        # Obtener o crear el plan "Free" para que se asigne automáticamente
        self.free_plan, _ = Plan.objects.get_or_create(
            name="Free",
            defaults={
                "max_daily_interactions": 10,
                "max_monthly_interactions": 100
            }
        )

    def test_user_registration(self):
        """Test user registration endpoint"""
        url = reverse('tenant_management:register')
        data = {
            'username': 'testuser',
            'password': 'testpass123',
            'email': 'test@example.com',
            'name': 'Test Tenant'
        }
        response = self.client.post(url, data, format='json', HTTP_X_FORWARDED_PROTO='https', secure=True)
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertIn('message', response.data)
        self.assertIn('email', response.data)
        self.assertEqual(response.data['email'], 'test@example.com')
        # Note: En el nuevo flujo, el usuario y tenant se crean tras verificación por email
        # Por eso estos asserts están comentados por ahora
        # self.assertTrue(User.objects.filter(email='test@example.com').exists())
        # self.assertTrue(TenantModel.objects.filter(name='Test Tenant').exists())

    def test_jwt_token_obtain(self):
        """Test JWT token obtain endpoint"""
        # First create a user
        user = User.objects.create_user(
            username='testuser',
            password='testpass123',
            email='test@example.com'
        )
        tenant = TenantModel.objects.create(
            name='Test Tenant',
            status='active'
        )
        TenantConfigurationModel.objects.create(tenant=tenant)
    
        # Create subscription for the tenant
        Subscription.objects.create(
            tenant=tenant,
            plan=self.free_plan,
            status='active'
        )
    
        # Associate user with tenant
        user.tenant = tenant
        user.save()
    
        url = reverse('tenant_management:token_obtain_pair')
        data = {
            'email': 'test@example.com',  # Changed from username to email
            'password': 'testpass123'
        }
        response = self.client.post(url, data, format='json', HTTP_X_FORWARDED_PROTO='https', secure=True)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn('access', response.data)
        self.assertIn('refresh', response.data)
        self.assertIn('access', response.data)
        self.assertIn('refresh', response.data)

class TenantAPITests(TestCase):
    def setUp(self):
        # Obtener o crear planes
        self.free_plan, _ = Plan.objects.get_or_create(
            name="Free",
            defaults={
                "max_daily_interactions": 10,
                "max_monthly_interactions": 100
            }
        )
        self.premium_plan, _ = Plan.objects.get_or_create(
            name="Premium",
            defaults={
                "max_daily_interactions": 100,
                "max_monthly_interactions": 1000,
                "price": 99.00
            }
        )
        
        # Crear usuarios y tenants
        self.user1 = User.objects.create_user(
            username='user1',
            password='pass123',
            email='user1@example.com'
        )
        self.tenant1 = TenantModel.objects.create(
            name='Tenant 1',
            status='active'
        )
        TenantConfigurationModel.objects.create(tenant=self.tenant1)
        Subscription.objects.create(
            tenant=self.tenant1,
            plan=self.free_plan,
            status='active'
        )
        self.user1.tenant = self.tenant1
        self.user1.save()

        self.user2 = User.objects.create_user(
            username='user2',
            password='pass123',
            email='user2@example.com'
        )
        self.tenant2 = TenantModel.objects.create(
            name='Tenant 2',
            status='active'
        )
        TenantConfigurationModel.objects.create(tenant=self.tenant2)
        Subscription.objects.create(
            tenant=self.tenant2,
            plan=self.free_plan,
            status='active'
        )
        self.user2.tenant = self.tenant2
        self.user2.save()

        self.client = APIClient()
        self.client.force_authenticate(user=self.user1)

    def test_list_tenants_authenticated(self):
        """Test listing tenants for authenticated user"""
        url = reverse('tenant_management:tenant-list')
        response = self.client.get(url, HTTP_X_FORWARDED_PROTO='https', secure=True)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        # Should only see their own tenant
        self.assertEqual(len(response.data), 1)
        self.assertEqual(response.data[0]['name'], 'Tenant 1')

    def test_list_tenants_unauthenticated(self):
        """Test listing tenants without authentication"""
        self.client.force_authenticate(user=None)
        url = reverse('tenant_management:tenant-list')
        response = self.client.get(url, HTTP_X_FORWARDED_PROTO='https', secure=True)
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_retrieve_own_tenant(self):
        """Test retrieving own tenant details"""
        url = reverse('tenant_management:tenant-detail', kwargs={'pk': self.tenant1.pk})
        response = self.client.get(url, HTTP_X_FORWARDED_PROTO='https', secure=True)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['name'], 'Tenant 1')

    def test_update_ai_configuration_success(self):
        """Test updating AI configuration"""
        url = reverse('tenant_management:tenant-update-ai-config', kwargs={'pk': self.tenant1.pk})
        data = {
            'provider': 'openai',
            'api_key': 'sk-test123',
            'model': 'gpt-3.5-turbo'
        }
        response = self.client.patch(url, data, format='json', HTTP_X_FORWARDED_PROTO='https', secure=True)
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        self.tenant1.configuration.refresh_from_db()
        self.assertEqual(self.tenant1.configuration.ai_settings, data)

    def test_change_plan_success(self):
        """Test changing tenant plan successfully"""
        url = reverse('tenant_management:tenant-change-plan', kwargs={'pk': self.tenant1.pk})
        data = {'plan_name': 'Premium'}
        response = self.client.post(url, data, format='json', HTTP_X_FORWARDED_PROTO='https', secure=True)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.tenant1.subscription.refresh_from_db()
        self.assertEqual(self.tenant1.subscription.plan.name, 'Premium')

    def test_change_plan_invalid_plan_name(self):
        """Test changing tenant plan with invalid plan name"""
        url = reverse('tenant_management:tenant-change-plan', kwargs={'pk': self.tenant1.pk})
        data = {'plan_name': 'NonExistentPlan'}
        response = self.client.post(url, data, format='json', HTTP_X_FORWARDED_PROTO='https', secure=True)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_change_plan_for_another_tenant_forbidden(self):
        """Test that a user cannot change another tenant's plan"""
        url = reverse('tenant_management:tenant-change-plan', kwargs={'pk': self.tenant2.pk})
        data = {'plan_name': 'Premium'}
        response = self.client.post(url, data, format='json', HTTP_X_FORWARDED_PROTO='https', secure=True)
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

class PlanAPITests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(
            username='testuser',
            password='testpass123'
        )
        self.client.force_authenticate(user=self.user)
        
        # Obtener o crear el plan "Free"
        self.free_plan, _ = Plan.objects.get_or_create(
            name="Free",
            defaults={
                "max_daily_interactions": 10,
                "max_monthly_interactions": 100
            }
        )

    def test_list_plans_authenticated(self):
        """Test listing plans when authenticated"""
        url = reverse('tenant_management:plan-list')
        response = self.client.get(url, HTTP_X_FORWARDED_PROTO='https', secure=True)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertGreater(len(response.data), 0)

    def test_list_plans_unauthenticated(self):
        """Test listing plans without authentication"""
        self.client.force_authenticate(user=None)
        url = reverse('tenant_management:plan-list')
        response = self.client.get(url, HTTP_X_FORWARDED_PROTO='https', secure=True)
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

class SubscriptionAPITests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(
            username='testuser',
            password='testpass123'
        )
        self.client.force_authenticate(user=self.user)
        
        # Obtener o crear el plan "Free"
        self.free_plan, _ = Plan.objects.get_or_create(
            name="Free",
            defaults={
                "max_daily_interactions": 10,
                "max_monthly_interactions": 100
            }
        )
        
        self.tenant = TenantModel.objects.create(
            name='Test Tenant',
            status='active'
        )
        TenantConfigurationModel.objects.create(tenant=self.tenant)
        self.subscription = Subscription.objects.create(
            tenant=self.tenant,
            plan=self.free_plan,
            status='active'
        )
        self.user.tenant = self.tenant
        self.user.save()

    def test_get_my_subscription_authenticated(self):
        """Test getting own subscription when authenticated"""
        url = reverse('tenant_management:subscription-detail', kwargs={'pk': self.subscription.pk})
        response = self.client.get(url, HTTP_X_FORWARDED_PROTO='https', secure=True)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['plan']['name'], 'Free')

    def test_get_my_subscription_unauthenticated(self):
        """Test getting subscription without authentication"""
        self.client.force_authenticate(user=None)
        url = reverse('tenant_management:subscription-detail', kwargs={'pk': self.subscription.pk})
        response = self.client.get(url, HTTP_X_FORWARDED_PROTO='https', secure=True)
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)