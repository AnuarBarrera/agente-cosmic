from django.urls import path
from . import views, auth_views, stripe_views

urlpatterns = [
    path('', views.home, name='landing'),
    path('nuevo-analisis/', views.new_analysis, name='new_analysis'),
    path('favicon.svg', views.favicon, name='favicon'),
    path('privacidad/', views.privacy_policy, name='privacy_policy'),
    path('terminos/', views.terms_of_service, name='terms_of_service'),
    path('analizar/', views.analyze_submit, name='analyze_submit'),
    path('resultados/<uuid:job_id>/', views.results, name='results'),
    path('api/brand-dna/status/<uuid:job_id>/', views.status_api, name='status_api'),
    path('api/brand-dna/product-photo-precheck/', views.product_photo_precheck_api, name='product_photo_precheck_api'),
    path('api/brand-dna/<uuid:job_id>/add-product-photos/', views.add_product_photos_api, name='add_product_photos_api'),

    # Auth
    path('auth/login/', auth_views.login_view, name='login'),
    path('auth/register/', auth_views.register_view, name='register'),
    path('auth/logout/', auth_views.logout_view, name='logout'),
    path('auth/verify/<str:token>/', auth_views.verify_email_view, name='verify_email'),
    path('auth/forgot-password/', auth_views.forgot_password_view, name='forgot_password'),
    path('auth/reset-password/<str:token>/', auth_views.reset_password_view, name='reset_password'),
    path('auth/google/', auth_views.google_login_view, name='google_login'),
    path('auth/google/callback/', auth_views.google_callback_view, name='google_callback'),
    path('dashboard/', auth_views.dashboard_view, name='dashboard'),
    path('dashboard/apply-code/', auth_views.apply_code_view, name='apply_code'),
    path('dashboard/delete-account/', auth_views.deactivate_account_view, name='deactivate_account'),
    path('auth/reactivate/<str:token>/', auth_views.reactivate_account_view, name='reactivate_account'),
    path('auth/entrar/<str:token>/', auth_views.magic_login_view, name='magic_login'),
    path('calendar/<uuid:job_id>/', views.calendar_review_view, name='calendar_review'),
    path('api/post/<uuid:post_id>/action/', views.post_action_api, name='post_action_api'),
    path('api/post/<uuid:post_id>/regen-status/', views.post_regen_status_api, name='post_regen_status_api'),
    path('api/post/<uuid:post_id>/download/', views.download_post_image, name='download_post_image'),
    path('api/calendar/<uuid:job_id>/delete/', views.delete_calendar_api, name='delete_calendar_api'),
    path('api/brand-dna/<uuid:job_id>/field/', views.brand_dna_field_action_api, name='brand_dna_field_action_api'),
    path('api/calendar/<uuid:job_id>/regenerate/', views.regenerate_calendar_api, name='regenerate_calendar_api'),
    path('stripe/webhook/', stripe_views.stripe_webhook_view, name='stripe_webhook'),
    path('dashboard/suscripcion/', stripe_views.manage_subscription_view, name='manage_subscription'),
]
