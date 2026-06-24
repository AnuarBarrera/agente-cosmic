from django.contrib import admin
from django.contrib.auth.models import Group
from django.db.models import Count
from django.http import Http404
from django.urls import reverse
from django.utils.html import format_html
from django_otp.admin import OTPAdminSite
from django_otp.plugins.otp_totp.admin import TOTPDeviceAdmin as OTPTOTPDeviceAdmin
from django_otp.plugins.otp_totp.models import TOTPDevice

from core.brand_dna.models import AnalysisJob
from core.tenant_management.models import (
    InvitationCode, Plan, SecurityEvent, User,
)


class CosmicAdminSite(OTPAdminSite):
    site_header = 'Agente Cosmic Admin'
    site_title = 'Agente Cosmic'
    index_title = 'Panel de administración'

    def has_permission(self, request):
        if not request.user.is_active or not request.user.is_staff:
            return False
        return super().has_permission(request)

    def login(self, request, extra_context=None):
        if request.method == 'GET' and request.user.is_authenticated and not request.user.is_staff:
            raise Http404
        return super().login(request, extra_context)

    def admin_view(self, view, cacheable=False):
        inner = super().admin_view(view, cacheable)

        def wrapper(request, *args, **kwargs):
            if request.user.is_authenticated and not request.user.is_staff:
                raise Http404
            return inner(request, *args, **kwargs)

        wrapper.__name__ = view.__name__
        wrapper.__module__ = view.__module__
        return wrapper


cosmic_admin = CosmicAdminSite(name='cosmic_admin')


class UserAdmin(admin.ModelAdmin):
    list_display = ('email', 'get_groups', 'is_active', 'date_joined', 'get_calendars_count')
    list_filter = ('is_active', 'groups', 'date_joined')
    search_fields = ('email', 'display_name')
    readonly_fields = ('id', 'date_joined', 'last_login')
    filter_horizontal = ('groups',)
    ordering = ('-date_joined',)

    def get_queryset(self, request):
        return super().get_queryset(request).annotate(
            calendars_count=Count('analysis_jobs', distinct=True)
        )

    def get_groups(self, obj):
        return ', '.join(g.name for g in obj.groups.all())
    get_groups.short_description = 'Grupos'

    def get_calendars_count(self, obj):
        return obj.calendars_count
    get_calendars_count.short_description = 'Calendarios'
    get_calendars_count.admin_order_field = 'calendars_count'

    actions = ['generate_invitation_codes']

    def generate_invitation_codes(self, request, queryset):
        codes = []
        for user in queryset:
            code = InvitationCode.objects.create(created_by=request.user)
            codes.append(f'{user.email}: {code.code}')
        self.message_user(request, 'Códigos generados: ' + ', '.join(codes))
    generate_invitation_codes.short_description = 'Generar código de invitación para seleccionados'


class InvitationCodeAdmin(admin.ModelAdmin):
    list_display = ('code', 'target_group', 'max_uses', 'times_used', 'is_active', 'expires_at', 'created_by', 'created_at')
    list_filter = ('is_active', 'target_group')
    search_fields = ('code',)
    readonly_fields = ('code', 'times_used', 'created_at')


class PlanAdmin(admin.ModelAdmin):
    list_display = ('name', 'max_calendars_per_week', 'max_post_regenerations', 'max_post_edits', 'price')


class AnalysisJobAdmin(admin.ModelAdmin):
    list_display = ('user', 'business_url', 'status', 'stage', 'created_at')
    list_filter = ('status', 'created_at')
    search_fields = ('email', 'business_url')

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


class SecurityEventAdmin(admin.ModelAdmin):
    list_display = ('event_type', 'user', 'ip_address', 'severity', 'created_at')
    list_filter = ('event_type', 'severity', 'created_at')
    search_fields = ('description', 'ip_address')

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


class TOTPDeviceAdmin(OTPTOTPDeviceAdmin):
    def qrcode_link(self, device):
        try:
            href = reverse(
                f'{self.admin_site.name}:otp_totp_totpdevice_config',
                kwargs={'pk': device.pk},
            )
            return format_html('<a href="{}">qrcode</a>', href)
        except Exception:
            return ''


cosmic_admin.register(User, UserAdmin)
cosmic_admin.register(InvitationCode, InvitationCodeAdmin)
cosmic_admin.register(Plan, PlanAdmin)
cosmic_admin.register(AnalysisJob, AnalysisJobAdmin)
cosmic_admin.register(SecurityEvent, SecurityEventAdmin)
cosmic_admin.register(TOTPDevice, TOTPDeviceAdmin)
cosmic_admin.register(Group)
