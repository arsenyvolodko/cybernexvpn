from django.conf import settings
from rest_framework.permissions import BasePermission


class IsAdmin(BasePermission):
    def has_permission(self, request, view):
        api_key = request.headers.get("X-API-KEY")
        return api_key == settings.ADMIN_API_KEY
