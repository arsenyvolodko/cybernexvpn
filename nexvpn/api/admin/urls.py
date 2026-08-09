from django.conf import settings
from django.contrib.admin.views.decorators import staff_member_required
from django.urls import path
from drf_spectacular.views import SpectacularAPIView, SpectacularRedocView, SpectacularSwaggerView

from nexvpn.api.admin.views.invitation import apply_invitation
from nexvpn.api.admin.views.payment import create_payment, get_transactions_history
from nexvpn.api.admin.views.promo_code import apply_promo_code
from nexvpn.api.admin.views.subscription import (
    change_plan,
    delete_device,
    get_subscription,
    list_devices,
    list_plans,
    quote_plan_change,
)
from nexvpn.api.admin.views.settings import get_maintenance
from nexvpn.api.admin.views.user import UsersViewSet, activate_user


def _docs_protected(view):
    return view if settings.DEBUG else staff_member_required(view)


urlpatterns = [
    path("users/", UsersViewSet.as_view({'get': 'list'})),
    path("users/<int:user_id>/", UsersViewSet.as_view({'get': 'retrieve', 'post': 'create', 'patch': 'partial_update'})),
    path("users/<int:user_id>/activate/", activate_user),

    path("users/<int:user_id>/payments/", create_payment),
    path("users/<int:user_id>/payments/history/", get_transactions_history),
    path("users/<int:user_id>/apply-invitation/", apply_invitation),
    path("users/<int:user_id>/apply-promo-code/", apply_promo_code),

    path("users/<int:user_id>/subscription/", get_subscription),
    path("users/<int:user_id>/subscription/quote-plan-change/", quote_plan_change),
    path("users/<int:user_id>/subscription/change-plan/", change_plan),
    path("users/<int:user_id>/subscription/devices/", list_devices),
    path("users/<int:user_id>/subscription/devices/<str:hwid>/", delete_device),

    path("plans/", list_plans),
    path("maintenance/", get_maintenance),

    path('docs/', _docs_protected(SpectacularSwaggerView.as_view(url_name='schema')), name='swagger-ui'),
    path('redoc/', _docs_protected(SpectacularRedocView.as_view(url_name='schema')), name='redoc'),
    path('schema/', _docs_protected(SpectacularAPIView.as_view()), name='schema'),
]
