from django.urls import path
from django.urls.conf import include

from .admin.urls import urlpatterns as admin_urls
from .notifications.urls import urlpatterns as yookassa_urls

urlpatterns = [
    path("admin/", include(admin_urls)),
    path("yookassa/", include(yookassa_urls)),
]
