from datetime import timedelta
from unittest.mock import patch, MagicMock
from django.test import TestCase
from django.utils import timezone


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
