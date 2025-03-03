from django.urls import path

from nexvpn.api.notifications.views import handle_notification

urlpatterns = [
    path("notifications/", handle_notification),
]
