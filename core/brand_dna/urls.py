from django.urls import path
from . import views, auth_views

urlpatterns = [
    path('', views.landing, name='landing'),
    path('favicon.svg', views.favicon, name='favicon'),
    path('analizar/', views.analyze_submit, name='analyze_submit'),
    path('resultados/<uuid:job_id>/', views.results, name='results'),
    path('api/brand-dna/status/<uuid:job_id>/', views.status_api, name='status_api'),

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
    path('calendar/<uuid:job_id>/', views.calendar_review_view, name='calendar_review'),
    path('api/post/<uuid:post_id>/action/', views.post_action_api, name='post_action_api'),
    path('api/calendar/<uuid:job_id>/delete/', views.delete_calendar_api, name='delete_calendar_api'),
    path('api/calendar/<uuid:job_id>/feedback/', views.calendar_feedback_api, name='calendar_feedback_api'),
]
