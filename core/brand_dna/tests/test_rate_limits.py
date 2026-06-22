from datetime import timedelta
from unittest.mock import patch, MagicMock
import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
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
            name='Free',
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
            name='Free',
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
            name='Free',
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
            name='Free',
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
            name='Free',
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
            name='Free',
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
            name='Free',
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
    free = Plan.objects.get_or_create(name='Free', defaults={
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
        user = User.objects.create_user(email='a@test.com', password='test123!', username='a@test.com')
        user.groups.add(all_groups['admin'])
        assert get_user_plan(user).name == 'Admin'

    def test_tester_group_gets_tester_plan(self, all_plans, all_groups):
        user = User.objects.create_user(email='t@test.com', password='test123!', username='t@test.com')
        user.groups.add(all_groups['tester'])
        assert get_user_plan(user).name == 'Tester'

    def test_user_group_gets_free_plan(self, all_plans, all_groups):
        user = User.objects.create_user(email='u@test.com', password='test123!', username='u@test.com')
        user.groups.add(all_groups['user'])
        assert get_user_plan(user).name == 'Free'

    def test_no_group_gets_free_plan(self, all_plans):
        user = User.objects.create_user(email='n@test.com', password='test123!', username='n@test.com')
        assert get_user_plan(user).name == 'Free'

    def test_tenant_subscription_takes_priority(self, all_plans, all_groups):
        tenant = TenantModel.objects.create(name='T2', status='active')
        Subscription.objects.create(tenant=tenant, plan=all_plans['free'])
        user = User.objects.create_user(email='p@test.com', password='test123!', username='p@test.com', tenant=tenant)
        user.groups.add(all_groups['admin'])
        assert get_user_plan(user).name == 'Free'
