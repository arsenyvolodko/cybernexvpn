from rest_framework import serializers

from nexvpn.models import Server, Endpoint


class ServerSerializer(serializers.ModelSerializer):

    has_available_ips = serializers.SerializerMethodField()

    @staticmethod
    def get_has_available_ips(obj: Server):
        return Endpoint.objects.filter(
            server_id=obj.id, client__isnull=True
        ).exists()

    class Meta:
        model = Server
        fields = ["id", "name", "price", "tag", "has_available_ips"]
