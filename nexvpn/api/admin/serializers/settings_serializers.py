from rest_framework import serializers

from nexvpn.models import GlobalSettings


class MaintenanceSerializer(serializers.ModelSerializer):
    class Meta:
        model = GlobalSettings
        fields = ("maintenance_mode", "maintenance_message", "maintenance_until")
