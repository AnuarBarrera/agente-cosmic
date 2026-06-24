"""
URL configuration for saas_chatbot project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/5.1/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static
from .views import health_check
from core.tenant_management.admin import cosmic_admin

handler400 = 'core.shared.error_handlers.handler400'
handler403 = 'core.shared.error_handlers.handler403'
handler404 = 'core.shared.error_handlers.handler404'
handler500 = 'core.shared.error_handlers.handler500'

urlpatterns = [
    path('health/', health_check, name='health'),
    path('admin/', cosmic_admin.urls),
    path('', include('django_prometheus.urls')),
    path('', include('core.brand_dna.urls')),
]

urlpatterns += static(settings.STATIC_URL, document_root=settings.STATIC_ROOT)
urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
