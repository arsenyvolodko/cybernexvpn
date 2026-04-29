from django.urls import path
from django.urls.conf import include

from .admin.urls import urlpatterns as admin_urls
from .notifications.urls import urlpatterns as yookassa_urls
from .notifications.views import handle_notification
from .user.views import me

urlpatterns = [
    path("admin/", include(admin_urls)),
    path("user/me/", me),
    path("payment_succeeded/", handle_notification),
    path("yookassa/", include(yookassa_urls)),
]
