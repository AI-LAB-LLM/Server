from rest_framework import serializers
from monitoring.utils import normalize_device_id


class PosInfoSerializer(serializers.Serializer):
    longitude = serializers.FloatField()
    latitude = serializers.FloatField()
    accuracy_h = serializers.FloatField()


class GeoLocationSerializer(serializers.Serializer):
    timestamp = serializers.DateTimeField()
    pos_success = serializers.BooleanField()
    pos_info = PosInfoSerializer(required=False, allow_null=True)

    def validate(self, attrs):
        pos_success = attrs.get("pos_success")
        pos_info = attrs.get("pos_info")

        if pos_success and not pos_info:
            raise serializers.ValidationError("pos_success가 true이면 pos_info는 필수입니다.")
        if not pos_success and pos_info:
            raise serializers.ValidationError("pos_success가 false이면 pos_info는 보내지 않아야 합니다.")
        return attrs


class GeoDataIngestSerializer(serializers.Serializer):
    device_id = serializers.CharField(max_length=100)
    locations = GeoLocationSerializer(many=True)

    def validate_device_id(self, value):
        return normalize_device_id(value)

    def validate_locations(self, value):
        if not value:
            raise serializers.ValidationError("locations는 최소 1개 이상이어야 합니다.")
        return value