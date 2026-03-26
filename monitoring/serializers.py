from rest_framework import serializers
from monitoring.utils import normalize_device_id


class IMUAlertSerializer(serializers.Serializer):
    device_id = serializers.CharField(max_length=100)
    timestamp = serializers.DateTimeField()
    imu_danger_level = serializers.IntegerField()

    def validate_device_id(self, value):
        return normalize_device_id(value)


class GEOAlertSerializer(serializers.Serializer):
    device_id = serializers.CharField(max_length=100)
    timestamp = serializers.DateTimeField()
    latitude = serializers.FloatField()
    longitude = serializers.FloatField()

    def validate_device_id(self, value):
        return normalize_device_id(value)