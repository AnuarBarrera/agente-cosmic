from django.urls import path
from . import views

urlpatterns = [
    path('', views.landing, name='landing'),
    path('analizar/', views.analyze_submit, name='analyze_submit'),
    path('resultados/<uuid:job_id>/', views.results, name='results'),
    path('api/brand-dna/status/<uuid:job_id>/', views.status_api, name='status_api'),
]
