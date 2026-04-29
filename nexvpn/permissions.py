from django.conf import settings
from rest_framework.exceptions import PermissionDenied
from rest_framework.permissions import BasePermission

from nexvpn.models import NexUser


class IsAdmin(BasePermission):
    def has_permission(self, request, view):
        api_key = request.headers.get("X-API-KEY")
        return api_key == settings.ADMIN_API_KEY


class IsUser(BasePermission):
    def has_permission(self, request, view):
        api_key = request.headers.get("X-API-KEY")
        if not api_key:
            return False
        nexuser = NexUser.objects.filter(token=api_key).first()
        if not nexuser:
            return False
        request.nexuser = nexuser
        return True


class IsAdminOrUser(BasePermission):
    def has_permission(self, request, view):
        api_key = request.headers.get("X-API-KEY")
        if not api_key:
            return False
        if api_key == settings.ADMIN_API_KEY:
            request.nexuser = None
            return True
        nexuser = NexUser.objects.filter(token=api_key).first()
        if not nexuser:
            return False
        request.nexuser = nexuser
        return True


def check_ownership(request, user_id: int) -> None:
    """Raises PermissionDenied if a non-admin user is accessing another user's resource."""
    if request.nexuser is not None and request.nexuser.id != user_id:
        raise PermissionDenied()
