from django.urls import path
from drf_spectacular.views import SpectacularSwaggerView, SpectacularRedocView, SpectacularAPIView

from nexvpn.api.admin.views.client import ClientsViewSet, reactivate_client, get_config_file, get_qr_file
from nexvpn.api.admin.views.server import ListServersView
from nexvpn.api.admin.views.transactions import get_transactions_history
from nexvpn.api.admin.views.user import UsersViewSet

urlpatterns = [
    path("users/<int:user_id>/", UsersViewSet.as_view({'get': 'retrieve', 'post': 'create'})),
    path("users/<int:user_id>/transactions-history/", get_transactions_history),

    path("users/<int:user_id>/clients/", ClientsViewSet.as_view({'get': 'list', 'post': 'create'})),
    path("users/<int:user_id>/clients/<int:client_id>/", ClientsViewSet.as_view({'get': 'retrieve', 'patch': 'partial_update', 'delete': 'destroy'})),
    path("users/<int:user_id>/clients/<int:client_id>/reactivate/", reactivate_client),
    path("users/<int:user_id>/clients/<int:client_id>/qr/", get_qr_file),
    path("users/<int:user_id>/clients/<int:client_id>/config/", get_config_file),

    path("servers/", ListServersView.as_view()),

    path('docs/', SpectacularSwaggerView.as_view(url_name='schema'), name='swagger-ui'),
    path('redoc/', SpectacularRedocView.as_view(url_name='schema'), name='redoc'),
    path('schema/', SpectacularAPIView.as_view(), name='schema'),
]
