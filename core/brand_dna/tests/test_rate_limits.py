import secrets
from datetime import timedelta
from unittest.mock import patch, MagicMock
import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group

_TEST_PWD = f"T3st-{secrets.token_urlsafe(10)}!"
from django.test import TestCase
from django.utils import timezone
from core.tenant_management.models import Plan, TenantModel, Subscription
from core.brand_dna.rate_limits import get_user_plan

User = get_user_model()


class TestGetUserPlan(TestCase):
    def test_returns_free_plan_when_no_tenant(self):
        from core.brand_dna.rate_limits import get_user_plan
        from core.tenant_management.models import Plan
        Plan.objects.get_or_create(
            name='User',
            defaults={'max_calendars_per_week': 2, 'max_post_regenerations': 2,
                      'max_post_edits': 2, 'price': '0.00'},
        )
        user = MagicMock()
        user.tenant = None
        plan = get_user_plan(user)
        assert plan.max_calendars_per_week == 2

    def test_returns_plan_from_subscription(self):
        from core.brand_dna.rate_limits import get_user_plan
        from core.tenant_management.models import Plan
        admin_plan, _ = Plan.objects.get_or_create(
            name='Admin',
            defaults={'max_calendars_per_week': 99999, 'max_post_regenerations': 99999,
                      'max_post_edits': 99999, 'price': '0.00'},
        )
        user = MagicMock()
        user.tenant.subscription.plan = admin_plan
        plan = get_user_plan(user)
        assert plan.max_calendars_per_week == 99999


class TestCanCreateCalendar(TestCase):
    def test_allowed_when_under_limit(self):
        from core.brand_dna.rate_limits import can_create_calendar
        from core.tenant_management.models import Plan
        Plan.objects.get_or_create(
            name='User',
            defaults={'max_calendars_per_week': 2, 'max_post_regenerations': 2,
                      'max_post_edits': 2, 'price': '0.00'},
        )
        user = MagicMock()
        user.tenant = None
        with patch('core.brand_dna.rate_limits.AnalysisJob') as MockJob:
            MockJob.objects.filter.return_value.count.return_value = 1
            allowed, remaining = can_create_calendar(user)
        assert allowed is True
        assert remaining == 1

    def test_blocked_when_at_limit(self):
        from core.brand_dna.rate_limits import can_create_calendar
        from core.tenant_management.models import Plan
        Plan.objects.get_or_create(
            name='User',
            defaults={'max_calendars_per_week': 2, 'max_post_regenerations': 2,
                      'max_post_edits': 2, 'price': '0.00'},
        )
        user = MagicMock()
        user.tenant = None
        with patch('core.brand_dna.rate_limits.AnalysisJob') as MockJob:
            MockJob.objects.filter.return_value.count.return_value = 2
            allowed, remaining = can_create_calendar(user)
        assert allowed is False
        assert remaining == 0


class TestCanRegenerate(TestCase):
    def _make_post(self, total_regens):
        post = MagicMock()
        post.calendar.posts.aggregate.return_value = {'total': total_regens}
        return post

    def test_allowed_when_under_limit(self):
        from core.brand_dna.rate_limits import can_regenerate
        from core.tenant_management.models import Plan
        Plan.objects.get_or_create(
            name='User',
            defaults={'max_calendars_per_week': 2, 'max_post_regenerations': 2,
                      'max_post_edits': 2, 'price': '0.00'},
        )
        user = MagicMock()
        user.tenant = None
        post = self._make_post(total_regens=1)
        allowed, remaining = can_regenerate(post, user)
        assert allowed is True
        assert remaining == 1

    def test_blocked_when_at_limit(self):
        from core.brand_dna.rate_limits import can_regenerate
        from core.tenant_management.models import Plan
        Plan.objects.get_or_create(
            name='User',
            defaults={'max_calendars_per_week': 2, 'max_post_regenerations': 2,
                      'max_post_edits': 2, 'price': '0.00'},
        )
        user = MagicMock()
        user.tenant = None
        post = self._make_post(total_regens=2)
        allowed, remaining = can_regenerate(post, user)
        assert allowed is False
        assert remaining == 0


class TestCanEdit(TestCase):
    def _make_post(self, total_edits):
        post = MagicMock()
        post.calendar.posts.aggregate.return_value = {'total': total_edits}
        return post

    def test_allowed_when_under_limit(self):
        from core.brand_dna.rate_limits import can_edit
        from core.tenant_management.models import Plan
        Plan.objects.get_or_create(
            name='User',
            defaults={'max_calendars_per_week': 2, 'max_post_regenerations': 2,
                      'max_post_edits': 2, 'price': '0.00'},
        )
        user = MagicMock()
        user.tenant = None
        post = self._make_post(total_edits=0)
        allowed, remaining = can_edit(post, user)
        assert allowed is True
        assert remaining == 2

    def test_blocked_when_at_limit(self):
        from core.brand_dna.rate_limits import can_edit
        from core.tenant_management.models import Plan
        Plan.objects.get_or_create(
            name='User',
            defaults={'max_calendars_per_week': 2, 'max_post_regenerations': 2,
                      'max_post_edits': 2, 'price': '0.00'},
        )
        user = MagicMock()
        user.tenant = None
        post = self._make_post(total_edits=2)
        allowed, remaining = can_edit(post, user)
        assert allowed is False
        assert remaining == 0


@pytest.fixture
def all_plans(db):
    free = Plan.objects.get_or_create(name='User', defaults={
        'max_calendars_per_week': 2, 'max_post_regenerations': 2, 'max_post_edits': 2,
    })[0]
    tester = Plan.objects.get_or_create(name='Tester', defaults={
        'max_calendars_per_week': 5, 'max_post_regenerations': 5, 'max_post_edits': 5,
    })[0]
    admin = Plan.objects.get_or_create(name='Admin', defaults={
        'max_calendars_per_week': 99999, 'max_post_regenerations': 99999, 'max_post_edits': 99999,
    })[0]
    return {'free': free, 'tester': tester, 'admin': admin}


@pytest.fixture
def all_groups(db):
    a, _ = Group.objects.get_or_create(name='admin')
    t, _ = Group.objects.get_or_create(name='tester')
    u, _ = Group.objects.get_or_create(name='user')
    return {'admin': a, 'tester': t, 'user': u}


@pytest.mark.django_db
class TestGetUserPlanByGroup:
    def test_admin_group_gets_admin_plan(self, all_plans, all_groups):
        user = User.objects.create_user(email='a@test.com', password=_TEST_PWD, username='a@test.com')
        user.groups.add(all_groups['admin'])
        assert get_user_plan(user).name == 'Admin'

    def test_tester_group_gets_tester_plan(self, all_plans, all_groups):
        user = User.objects.create_user(email='t@test.com', password=_TEST_PWD, username='t@test.com')
        user.groups.add(all_groups['tester'])
        assert get_user_plan(user).name == 'Tester'

    def test_user_group_gets_free_plan(self, all_plans, all_groups):
        user = User.objects.create_user(email='u@test.com', password=_TEST_PWD, username='u@test.com')
        user.groups.add(all_groups['user'])
        assert get_user_plan(user).name == 'User'

    def test_no_group_gets_free_plan(self, all_plans):
        user = User.objects.create_user(email='n@test.com', password=_TEST_PWD, username='n@test.com')
        assert get_user_plan(user).name == 'User'

    def test_tenant_subscription_takes_priority(self, all_plans, all_groups):
        tenant = TenantModel.objects.create(name='T2', status='active')
        Subscription.objects.create(tenant=tenant, plan=all_plans['free'])
        user = User.objects.create_user(email='p@test.com', password=_TEST_PWD, username='p@test.com', tenant=tenant)
        user.groups.add(all_groups['admin'])
        assert get_user_plan(user).name == 'User'


class TestCanPrecheckPhoto(TestCase):
    def test_allowed_when_under_limit(self):
        from core.brand_dna.rate_limits import can_precheck_photo
        from core.tenant_management.models import Plan
        Plan.objects.get_or_create(
            name='User',
            defaults={'max_calendars_per_week': 2, 'max_post_regenerations': 2,
                      'max_post_edits': 2, 'max_photo_prechecks_per_day': 10, 'price': '0.00'},
        )
        user = MagicMock()
        user.tenant = None
        with patch('core.brand_dna.rate_limits.ProductPhotoPrecheckAttempt') as MockAttempt:
            MockAttempt.objects.filter.return_value.count.return_value = 3
            allowed, remaining = can_precheck_photo(user)
        assert allowed is True
        assert remaining == 7

    def test_blocked_when_at_limit(self):
        from core.brand_dna.rate_limits import can_precheck_photo
        from core.tenant_management.models import Plan
        Plan.objects.get_or_create(
            name='User',
            defaults={'max_calendars_per_week': 2, 'max_post_regenerations': 2,
                      'max_post_edits': 2, 'max_photo_prechecks_per_day': 10, 'price': '0.00'},
        )
        user = MagicMock()
        user.tenant = None
        with patch('core.brand_dna.rate_limits.ProductPhotoPrecheckAttempt') as MockAttempt:
            MockAttempt.objects.filter.return_value.count.return_value = 10
            allowed, remaining = can_precheck_photo(user)
        assert allowed is False
        assert remaining == 0

    def test_only_counts_attempts_within_last_24h(self):
        """Un intento de hace 25h no debe contar contra el límite del día."""
        from core.brand_dna.rate_limits import can_precheck_photo
        from core.brand_dna.models import ProductPhotoPrecheckAttempt
        from core.tenant_management.models import Plan, TenantModel, Subscription
        from django.contrib.auth import get_user_model
        User = get_user_model()
        plan, _ = Plan.objects.get_or_create(
            name='User',
            defaults={'max_calendars_per_week': 2, 'max_post_regenerations': 2,
                      'max_post_edits': 2, 'max_photo_prechecks_per_day': 10, 'price': '0.00'},
        )
        user = User.objects.create_user(
            username='precheck-window@test.com', email='precheck-window@test.com', password='pass1234',
        )
        tenant = TenantModel.objects.create(name=user.email, status='active')
        Subscription.objects.create(tenant=tenant, plan=plan)
        user.tenant = tenant
        user.save(update_fields=['tenant'])

        old_attempt = ProductPhotoPrecheckAttempt.objects.create(user=user)
        old_attempt.created_at = timezone.now() - timedelta(hours=25)
        old_attempt.save(update_fields=['created_at'])

        allowed, remaining = can_precheck_photo(user)
        assert allowed is True
        assert remaining == 10


# ==== Tests for get_payment_url (Task 2) ====
from django.test import override_settings
import pytest


def _user_with_plan(plan):
    user = User.objects.create_user(
        username=f'{plan.name}@test.com', email=f'{plan.name}@test.com', password=_TEST_PWD,
    )
    tenant = TenantModel.objects.create(name=user.email, status='active')
    Subscription.objects.create(tenant=tenant, plan=plan)
    user.tenant = tenant
    user.save(update_fields=['tenant'])
    return user


@pytest.mark.django_db
@override_settings(STRIPE_PAYMENT_LINK_URL='https://buy.stripe.com/global123')
def test_get_payment_url_falls_back_to_global_link_when_plan_has_none():
    from core.brand_dna.rate_limits import get_payment_url
    plan = Plan.objects.create(name='Plan Sin Link')
    user = _user_with_plan(plan)
    url = get_payment_url(user)
    assert url == f'https://buy.stripe.com/global123?client_reference_id={user.tenant_id}'


@pytest.mark.django_db
@override_settings(STRIPE_PAYMENT_LINK_URL='https://buy.stripe.com/global123')
def test_get_payment_url_uses_plan_specific_link_when_set():
    from core.brand_dna.rate_limits import get_payment_url
    plan = Plan.objects.create(
        name='Plan Con Link', stripe_payment_link_url='https://buy.stripe.com/founder123',
    )
    user = _user_with_plan(plan)
    url = get_payment_url(user)
    assert url == f'https://buy.stripe.com/founder123?client_reference_id={user.tenant_id}'


@pytest.mark.django_db
def test_can_create_calendar_excludes_soft_deleted_jobs_from_quota():
    from core.brand_dna.rate_limits import can_create_calendar
    from core.brand_dna.models import AnalysisJob
    plan = Plan.objects.create(name='Plan Quota Test', max_calendars_per_week=1)
    user = _user_with_plan(plan)
    job = AnalysisJob.objects.create(email=user.email, business_url='https://tuwebmx.com', user=user)
    job.deleted_at = timezone.now()
    job.save(update_fields=['deleted_at'])

    allowed, remaining = can_create_calendar(user)

    assert allowed is True
    assert remaining == 1
