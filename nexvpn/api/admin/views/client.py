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
from rest_framework.decorators import api_view, permission_classes
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.viewsets import ModelViewSet

from nexvpn import permissions
from nexvpn.api.admin.serializers.client_serializers import ClientSerializer
from nexvpn.api.exceptions.base_client_error import BaseClientError
from nexvpn.api.exceptions.enums.error_message_enum import ErrorMessageEnum
from nexvpn.api.exceptions.no_free_endpoints_error import NoFreeEndpoints
from nexvpn.api_clients.wg_api_client.schemas import CreateClientRequest, DeleteClientRequest
from nexvpn.utils import add_client, delete_client, get_config_schema, gen_client_config_data
from nexvpn.enums import TransactionTypeEnum, ClientUpdatesEnum
from nexvpn.models import Client, UserBalance, Endpoint, Transaction, NexUser, ClientUpdates


@extend_schema(tags=["client"])
@permission_classes([permissions.IsAdmin])
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

            ClientUpdates.objects.create(
                user=client.user,
                client=client,
                action=ClientUpdatesEnum.CREATED,
                automatically=False
            )

            endpoint.client = client
            endpoint.save()

            config_schema = get_config_schema(client)
            create_client_request = CreateClientRequest(ip=client.endpoint.ip, public_key=client.public_key)
            asyncio.run(add_client(config_schema, create_client_request))

    def perform_destroy(self, instance: Client) -> None:
        with transaction.atomic():

            ClientUpdates.objects.create(
                user=instance.user,
                client=None,
                action=ClientUpdatesEnum.DELETED,
                automatically=False
            )

            config_schema = get_config_schema(instance)
            public_key = instance.public_key
            instance.delete()

            delete_client_request = DeleteClientRequest(public_key=public_key)
            asyncio.run(delete_client(config_schema, delete_client_request))

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


@extend_schema(tags=["client"], responses={200: ClientSerializer})
@api_view(["POST"])
@permission_classes([permissions.IsAdmin])
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

            ClientUpdates.objects.create(
                user=client.user,
                client=client,
                action=ClientUpdatesEnum.RENEWED,
                automatically=False
            )

            client.is_active = True
            client.auto_renew = True
            client.end_date = (now() + relativedelta(months=1)).date()
            client.save()

            config_schema = get_config_schema(client)
            create_client_request = CreateClientRequest(ip=client.endpoint.ip, public_key=client.public_key)
            asyncio.run(add_client(config_schema, create_client_request))

        return Response(status=status.HTTP_200_OK, data=ClientSerializer(client).data)

    except BaseClientError as e:
        logging.info(f"Cannot reactivate client: {e.message}")
        data = {"error_message": e.message}
    except Exception as e:
        data = {"detail": str(e)}
        logging.error(f"Error reactivating client: {e}", exc_info=True)

    return Response(data, status=status.HTTP_400_BAD_REQUEST)


@extend_schema(tags=["client"])
@api_view(["GET"])
@permission_classes([permissions.IsAdmin])
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
@permission_classes([permissions.IsAdmin])
def get_qr_file(request: Request, user_id: int, client_id: int) -> FileResponse:
    client = get_object_or_404(Client, pk=client_id, user_id=user_id)
    client_data = gen_client_config_data(client)

    with tempfile.NamedTemporaryFile(mode="wb+", delete=True) as temp_file:
        qr = qrcode.make(client_data)
        qr.save(temp_file)
        response = FileResponse(open(temp_file.name, "rb"), as_attachment=True, content_type="image/png")
        return response
