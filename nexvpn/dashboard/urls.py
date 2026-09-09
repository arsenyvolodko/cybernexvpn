from django.urls import path

from . import views

urlpatterns = [
    path("", views.page, name="dashboard"),
    path("api/overview/", views.overview),
    path("api/payments/", views.payments),
    path("api/users/", views.users),
    path("api/tunnels/", views.tunnels),
    path("api/users/<int:user_id>/", views.user_card),
]
