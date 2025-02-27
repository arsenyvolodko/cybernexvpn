from django.urls import path
from django.urls.conf import include
from drf_spectacular.views import SpectacularSwaggerView, SpectacularAPIView, SpectacularRedocView

from .admin.urls import urlpatterns as admin_urls

urlpatterns = [
    path("admin/", include(admin_urls)),
]
