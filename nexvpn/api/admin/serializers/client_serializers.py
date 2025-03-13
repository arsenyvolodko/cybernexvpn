from rest_framework import serializers

from nexvpn.enums import ClientTypeEnum
from nexvpn.models import Client, Server


class ClientSerializer(serializers.ModelSerializer):
    name = serializers.CharField(max_length=63, required=False)
    type = serializers.ChoiceField(choices=ClientTypeEnum.choices, default=ClientTypeEnum.UNKNOWN, required=False)
    server = serializers.PrimaryKeyRelatedField(queryset=Server.objects.filter(is_active=True), write_only=True)
    server_name = serializers.CharField(source='server.name', read_only=True)
    price = serializers.IntegerField(source='server.price', read_only=True)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if 'request' in self.context:
            if self.context['request'].method == 'PATCH':
                self.fields['server'].read_only = True
            if self.context['request'].method == 'POST':
                self.fields.pop('auto_renew')

    class Meta:
        model = Client
        fields = ["id", "name", "is_active", "end_date", "server", "server_name", "price", "auto_renew", "type"]
        read_only_fields = ["id", "is_active", "end_date"]
