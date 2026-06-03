from datetime import timedelta
from django.utils import timezone
from core.brand_dna.models import AnalysisJob


def get_user_plan(user):
    from core.tenant_management.models import Plan
    try:
        return user.tenant.subscription.plan
    except Exception:
        return Plan.objects.filter(name='Free').first() or Plan(
            max_calendars_per_week=2,
            max_post_regenerations=2,
            max_post_edits=2,
        )


def can_create_calendar(user) -> tuple[bool, int]:
    plan = get_user_plan(user)
    week_ago = timezone.now() - timedelta(days=7)
    used = AnalysisJob.objects.filter(user=user, created_at__gte=week_ago).count()
    remaining = max(0, plan.max_calendars_per_week - used)
    return remaining > 0, remaining


def can_regenerate(post, user) -> tuple[bool, int]:
    plan = get_user_plan(user)
    remaining = max(0, plan.max_post_regenerations - post.regen_count)
    return remaining > 0, remaining


def can_edit(post, user) -> tuple[bool, int]:
    plan = get_user_plan(user)
    remaining = max(0, plan.max_post_edits - post.edit_count)
    return remaining > 0, remaining
