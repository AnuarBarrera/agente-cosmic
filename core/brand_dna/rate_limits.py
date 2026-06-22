from datetime import timedelta
from django.utils import timezone
from core.brand_dna.models import AnalysisJob


def get_user_plan(user):
    from core.tenant_management.models import Plan
    try:
        return user.tenant.subscription.plan
    except Exception:
        pass
    _GROUP_TO_PLAN = {'admin': 'Admin', 'tester': 'Tester', 'user': 'Free'}
    group_names = set(user.groups.values_list('name', flat=True))
    for group_name, plan_name in _GROUP_TO_PLAN.items():
        if group_name in group_names:
            plan = Plan.objects.filter(name=plan_name).first()
            if plan:
                return plan
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
    """Límite de regeneraciones por calendario completo (suma de todos los posts)."""
    from django.db.models import Sum
    plan = get_user_plan(user)
    calendar = post.calendar
    total_used = calendar.posts.aggregate(total=Sum('regen_count'))['total'] or 0
    remaining = max(0, plan.max_post_regenerations - total_used)
    return remaining > 0, remaining


def can_edit(post, user) -> tuple[bool, int]:
    """Límite de ediciones por calendario completo (suma de todos los posts)."""
    from django.db.models import Sum
    plan = get_user_plan(user)
    calendar = post.calendar
    total_used = calendar.posts.aggregate(total=Sum('edit_count'))['total'] or 0
    remaining = max(0, plan.max_post_edits - total_used)
    return remaining > 0, remaining
