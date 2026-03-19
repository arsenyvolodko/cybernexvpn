import asyncio
import logging
import secrets

from dateutil.relativedelta import relativedelta
from django.db import transaction
from django.db.models import QuerySet
from django.shortcuts import get_object_or_404
from django.utils.timezone import now
from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.viewsets import ModelViewSet

from nexvpn import permissions
from nexvpn.api.admin.serializers.proxy_client_serializers import ProxyClientSerializer
from nexvpn.api.exceptions.base_client_error import BaseClientError
from nexvpn.api.exceptions.enums.error_message_enum import ErrorMessageEnum
from nexvpn.api_clients.telemt_api_client.schemas import CreateTelemtClientRequest, DeleteTelemtClientRequest
from nexvpn.enums import TransactionTypeEnum
from nexvpn.models import Client, UserBalance, Transaction, NexUser, ProxyClient
from nexvpn.proxy_utils import add_proxy_client, delete_proxy_client, get_proxy_server_config, get_proxy_url
from nexvpn.wg_utils import get_telemt_config_schema


def generate_unique_secret():
    while True:
        secret = secrets.token_hex(16)
        if not ProxyClient.objects.filter(secret=secret).exists():
            return secret


@extend_schema(tags=["proxy-client"])
@permission_classes([permissions.IsAdmin])
class ProxyClientsViewSet(ModelViewSet):
    queryset = ProxyClient.objects.all()
    serializer_class = ProxyClientSerializer
    lookup_url_kwarg = "client_id"

    def filter_queryset(self, queryset) -> QuerySet[Client]:
        user_id = self.kwargs.get("user_id")
        return queryset.filter(user_id=user_id).all()

    def perform_create(self, serializer: ProxyClientSerializer, *args, **kwargs) -> None:
        user_id = self.kwargs.get("user_id")
        proxy_server = get_proxy_server_config()
        user = get_object_or_404(NexUser, pk=user_id)

        with transaction.atomic():

            if proxy_server.price > 0:
                user_balance = UserBalance.objects.select_for_update().get(user=user)
                if user_balance.value < proxy_server.price:
                    raise BaseClientError(
                        ErrorMessageEnum.NOT_ENOUGH_MONEY_TO_ADD_CLIENT_ERROR_MESSAGE.value
                    )
                user_balance.value -= proxy_server.price
                user_balance.save()

            Transaction.objects.create(
                user=user,
                is_credit=False,
                value=proxy_server.price,
                type=TransactionTypeEnum.ADD_PROXY
            )

            secret = generate_unique_secret()

            serializer.save(
                user=user,
                end_date=(now() + relativedelta(months=1)).date(),
                secret=secret
            )

            create_proxy_client_request = CreateTelemtClientRequest(
                telegram_id=user_id,
                secret=secret
            )
            telemt_config_schema = get_telemt_config_schema(proxy_server)
            asyncio.run(add_proxy_client(telemt_config_schema, create_proxy_client_request))

    def perform_destroy(self, instance: ProxyClient) -> None:
        proxy_server = get_proxy_server_config()

        with transaction.atomic():
            config_schema = get_telemt_config_schema(proxy_server)
            instance.is_active = False
            instance.auto_renew = False
            instance.save()

            delete_client_request = DeleteTelemtClientRequest(telegram_id=str(instance.user.id))
            asyncio.run(delete_proxy_client(config_schema, delete_client_request))

    def create(self, request: Request, *args, **kwargs) -> Response:
        try:
            return super().create(request, *args, **kwargs)
        except BaseClientError as e:
            data = {"error_message": e.message}
            logging.info(f"Cannot create client: {e.message}")
        except Exception as e:
            data = {"detail": str(e)}
            logging.error(f"Error creating client: {e}", exc_info=True)
        return Response(data, status=status.HTTP_400_BAD_REQUEST)

    def destroy(self, request: Request, *args, **kwargs) -> Response:
        try:
            return super().destroy(request, *args, **kwargs)
        except Exception as e:
            data = {"detail": str(e)}
        return Response(data, status=status.HTTP_400_BAD_REQUEST)


@extend_schema(tags=["proxy-client"], responses={200: ProxyClientSerializer})
@api_view(["POST"])
@permission_classes([permissions.IsAdmin])
def reactivate_proxy_client(request: Request, user_id: int, client_id: int) -> Response:  # noqa
    client: ProxyClient = get_object_or_404(ProxyClient, pk=client_id, user_id=user_id)

    try:
        if client.is_active:
            raise BaseClientError(
                ErrorMessageEnum.PROXY_CLIENT_IS_ALREADY_ACTIVE_ERROR_MESSAGE.value
            )

        with transaction.atomic():
            proxy_server = get_proxy_server_config()
            user_balance = UserBalance.objects.select_for_update().get(user_id=user_id)

            if user_balance.value < proxy_server.price:
                raise BaseClientError(
                    ErrorMessageEnum.NOT_ENOUGH_MONEY_TO_ACTIVATE_PROXY_ERROR_MESSAGE.value
                )

            price = proxy_server.price

            if price > 0:
                user_balance.value -= price
                user_balance.save()

                Transaction.objects.create(
                    user_id=user_id,
                    is_credit=False,
                    value=price,
                    type=TransactionTypeEnum.REACTIVATE_PROXY_SUBSCRIPTION
                )

            client.is_active = True
            client.auto_renew = True
            client.end_date = (now() + relativedelta(months=1)).date()
            client.save()

            config_schema = get_telemt_config_schema(proxy_server)
            create_client_request = CreateTelemtClientRequest(telegram_id=str(user_id), secret=client.secret)
            asyncio.run(add_proxy_client(config_schema, create_client_request))

        return Response(status=status.HTTP_200_OK, data=ProxyClientSerializer(client).data)

    except BaseClientError as e:
        logging.info(f"Cannot reactivate client: {e.message}")
        data = {"error_message": e.message}
    except Exception as e:
        data = {"detail": str(e)}
        logging.error(f"Error reactivating client: {e}", exc_info=True)

    return Response(data, status=status.HTTP_400_BAD_REQUEST)
