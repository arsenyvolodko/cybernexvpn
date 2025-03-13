from django.conf import settings
from rest_framework.authentication import BaseAuthentication
from rest_framework.exceptions import AuthenticationFailed

ALLOWED_PATHS = [
    '/api/v1/admin/docs/',
    '/api/v1/admin/schema/'
]


class APIKeyAuthentication(BaseAuthentication):
    def authenticate(self, request):

        if any(request.path.startswith(allowed_path) for allowed_path in ALLOWED_PATHS):
            return None

        if request.path.startswith('/api/v1/admin/'):
            api_key = request.headers.get('X-API-KEY')
            if not api_key or api_key != settings.ADMIN_API_KEY:
                raise AuthenticationFailed('Invalid API Key')
        return None
