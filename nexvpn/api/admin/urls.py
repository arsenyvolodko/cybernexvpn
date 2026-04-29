from django.conf import settings
from django.contrib.admin.views.decorators import staff_member_required
from django.urls import path
from drf_spectacular.views import SpectacularSwaggerView, SpectacularRedocView, SpectacularAPIView

from nexvpn.api.admin.views.client import ClientsViewSet, reactivate_client, get_config_file, get_qr_file
from nexvpn.api.admin.views.invitation import apply_invitation
from nexvpn.api.admin.views.promo_code import apply_promo_code
from nexvpn.api.admin.views.server import ListServersView, RetrieveServerView
from nexvpn.api.admin.views.payment import get_transactions_history, create_payment
from nexvpn.api.admin.views.user import UsersViewSet


def _docs_protected(view):
    return view if settings.DEBUG else staff_member_required(view)

urlpatterns = [
    path("users/", UsersViewSet.as_view({'get': 'list'})),
    path("users/<int:user_id>/", UsersViewSet.as_view({'get': 'retrieve', 'post': 'create', 'patch': 'partial_update'})),

    path("users/<int:user_id>/payments/", create_payment),
    path("users/<int:user_id>/payments/history/", get_transactions_history),
    path("users/<int:user_id>/apply-invitation/", apply_invitation),
    path("users/<int:user_id>/apply-promo-code/", apply_promo_code),

    path("users/<int:user_id>/clients/", ClientsViewSet.as_view({'get': 'list', 'post': 'create'})),
    path("users/<int:user_id>/clients/<int:client_id>/", ClientsViewSet.as_view({'get': 'retrieve', 'patch': 'partial_update', 'delete': 'destroy'})),
    path("users/<int:user_id>/clients/<int:client_id>/reactivate/", reactivate_client),
    path("users/<int:user_id>/clients/<int:client_id>/qr/", get_qr_file),
    path("users/<int:user_id>/clients/<int:client_id>/config/", get_config_file),

    path("servers/", ListServersView.as_view()),
    path("servers/<int:server_id>/", RetrieveServerView.as_view()),

    path('docs/', _docs_protected(SpectacularSwaggerView.as_view(url_name='schema')), name='swagger-ui'),
    path('redoc/', _docs_protected(SpectacularRedocView.as_view(url_name='schema')), name='redoc'),
    path('schema/', _docs_protected(SpectacularAPIView.as_view()), name='schema'),
]
