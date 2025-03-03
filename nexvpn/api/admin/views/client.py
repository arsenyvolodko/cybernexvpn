import asyncio
import logging
import tempfile

import qrcode
from dateutil.relativedelta import relativedelta
from django.db import transaction
from django.db.models import QuerySet
from django.http import FileResponse
from django.shortcuts import get_object_or_404
from django.utils.timezone import now
from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.decorators import api_view
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.viewsets import ModelViewSet

from nexvpn.api.admin.serializers.client_serializers import ClientSerializer
from nexvpn.api.exceptions.base_client_error import BaseClientError
from nexvpn.api.exceptions.enums.error_message_enum import ErrorMessageEnum
from nexvpn.api.exceptions.no_free_endpoints_error import NoFreeEndpoints
from nexvpn.api_clients.schemas import CreateClientRequest
from nexvpn.api.utils.api_client_utils import add_client, delete_client, get_config_schema, gen_client_config_data
from nexvpn.enums import TransactionTypeEnum
from nexvpn.models import Client, UserBalance, Endpoint, Transaction, NexUser


@extend_schema(tags=["client"])
class ClientsViewSet(ModelViewSet):
    queryset = Client.objects.all()
    serializer_class = ClientSerializer
    lookup_url_kwarg = "client_id"

    def filter_queryset(self, queryset) -> QuerySet[Client]:
        user_id = self.kwargs.get("user_id")
        if client_id := self.kwargs.get("client_id"):
            queryset = queryset.filter(pk=client_id)
        return queryset.filter(user_id=user_id)

    def perform_create(self, serializer: ClientSerializer, *args, **kwargs) -> None:
        user_id = self.kwargs.get("user_id")
        server = serializer.validated_data["server"]

        user = get_object_or_404(NexUser, pk=user_id)

        with transaction.atomic():

            endpoint = Endpoint.objects.select_for_update().filter(
                server=server,
                client__isnull=True
            ).first()

            if not endpoint:
                raise NoFreeEndpoints()

            user_balance = UserBalance.objects.select_for_update().get(user=user)
            if user_balance.value < server.price:
                raise BaseClientError(
                    ErrorMessageEnum.NOT_ENOUGH_MONEY_TO_ADD_CLIENT_ERROR_MESSAGE.value
                )
            user_balance.value -= server.price
            user_balance.save()

            Transaction.objects.create(
                user=user,
                is_credit=False,
                value=server.price,
                type=TransactionTypeEnum.ADD_DEVICE
            )

            client = serializer.save(
                server=server,
                user=user
            )

            endpoint.client = client
            endpoint.save()

            config_schema = get_config_schema(client)
            create_client_request = CreateClientRequest(ip=client.endpoint.ip)
            asyncio.run(add_client(config_schema, client.public_key, create_client_request))

    def perform_destroy(self, instance: Client) -> None:
        with transaction.atomic():
            config_schema = get_config_schema(instance)
            public_key = instance.public_key
            instance.delete()
            asyncio.run(delete_client(config_schema, public_key))

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


@extend_schema(tags=["client"])
@api_view(["POST"])
def reactivate_client(request: Request, user_id: int, client_id: int) -> Response:  # noqa
    client = get_object_or_404(Client, pk=client_id, user_id=user_id)

    try:
        if client.is_active:
            raise BaseClientError(
                ErrorMessageEnum.CLIENT_IS_ALREADY_ACTIVE_ERROR_MESSAGE.value
            )

        with transaction.atomic():
            price = client.server.price

            user_balance = UserBalance.objects.select_for_update().get(user_id=user_id)
            if user_balance.value < price:
                raise BaseClientError(
                    ErrorMessageEnum.NOT_ENOUGH_MONEY_TO_ADD_CLIENT_ERROR_MESSAGE.value
                )

            user_balance.value -= price
            user_balance.save()

            Transaction.objects.create(
                user_id=user_id,
                is_credit=False,
                value=price,
                type=TransactionTypeEnum.REACTIVATE_CLIENT
            )

            client.is_active = True
            client.auto_renew = True
            client.end_date = (now() + relativedelta(months=1)).date()
            client.save()

            config_schema = get_config_schema(client)
            create_client_request = CreateClientRequest(ip=client.endpoint.ip)
            asyncio.run(add_client(config_schema, client.public_key, create_client_request))

        return Response(status=status.HTTP_200_OK)

    except BaseClientError as e:
        logging.info(f"Cannot reactivate client: {e.message}")
        data = {"error_message": e.message}
    except Exception as e:
        data = {"detail": str(e)}
        logging.error(f"Error reactivating client: {e}", exc_info=True)

    return Response(data, status=status.HTTP_400_BAD_REQUEST)


@extend_schema(tags=["client"])
@api_view(["GET"])
def get_config_file(request: Request, user_id: int, client_id: int) -> FileResponse:
    client = get_object_or_404(Client, pk=client_id, user_id=user_id)
    client_data = gen_client_config_data(client)

    with tempfile.NamedTemporaryFile(mode="w+", delete=True) as temp_file:
        temp_file.write(client_data)
        temp_file.flush()
        response = FileResponse(open(temp_file.name, "rb"), as_attachment=True, filename=f"{client.name}.conf")
        return response


@extend_schema(tags=["client"])
@api_view(["GET"])
def get_qr_file(request: Request, user_id: int, client_id: int) -> FileResponse:
    client = get_object_or_404(Client, pk=client_id, user_id=user_id)
    client_data = gen_client_config_data(client)

    with tempfile.NamedTemporaryFile(mode="wb+", delete=True) as temp_file:
        qr = qrcode.make(client_data)
        qr.save(temp_file)
        response = FileResponse(open(temp_file.name, "rb"), as_attachment=True, content_type="image/png")
        return response
