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
    latitude = serializers.FloatField(required=False, allow_null=True)
    longitude = serializers.FloatField(required=False, allow_null=True)

    def validate_device_id(self, value):
        return normalize_device_id(value)

    def validate(self, attrs):
        latitude = attrs.get("latitude")
        longitude = attrs.get("longitude")

        if (latitude is None and longitude is not None) or (latitude is not None and longitude is None):
            raise serializers.ValidationError(
                "latitude와 longitude는 둘 다 있거나, 둘 다 null이어야 합니다."
            )

        return attrs