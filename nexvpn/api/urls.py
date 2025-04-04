from django.urls import path
from django.urls.conf import include

from .admin.urls import urlpatterns as admin_urls
from .notifications.urls import urlpatterns as yookassa_urls
from .notifications.views import handle_notification

urlpatterns = [
    path("admin/", include(admin_urls)),
    path("payment_succeeded/", handle_notification),
    path("yookassa/", include(yookassa_urls)),
]
