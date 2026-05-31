from django.urls import path
from . import views
from .n8n_views import n8n_callback

urlpatterns = [
    path('health/', views.agent_health, name='agent_health'),
    path('metrics/', views.agent_metrics, name='agent_metrics'),
    path('n8n/callback/', n8n_callback, name='n8n_callback'),
]
