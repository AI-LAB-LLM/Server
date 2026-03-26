from rest_framework import serializers
from monitoring.utils import normalize_device_id
from .fields import UnixMsDateTimeField


class PosInfoSerializer(serializers.Serializer):
    longitude = serializers.FloatField(help_text="경도")
    latitude = serializers.FloatField(help_text="위도")
    accuracy_h = serializers.FloatField(help_text="수평 정확도(m)")


class GeoLocationSerializer(serializers.Serializer):
    timestamp = UnixMsDateTimeField(help_text="위치 수집 시간 (UNIX time, ms)")
    pos_success = serializers.BooleanField(help_text="위치 수집 성공 여부")
    pos_info = PosInfoSerializer(
        required=False,
        allow_null=True,
        help_text="pos_success=true 일 때만 포함"
    )

    def validate(self, attrs):
        pos_success = attrs.get("pos_success")
        pos_info = attrs.get("pos_info")

        if pos_success and not pos_info:
            raise serializers.ValidationError(
                "pos_success가 true이면 pos_info는 필수입니다."
            )

        if not pos_success and pos_info:
            raise serializers.ValidationError(
                "pos_success가 false이면 pos_info는 보내지 않아야 합니다."
            )

        return attrs


class GeoDataIngestSerializer(serializers.Serializer):
    device_id = serializers.CharField(
        max_length=100,
        help_text="워치 고유 ID"
    )
    locations = GeoLocationSerializer(
        many=True,
        help_text="위치 데이터 배열"
    )

    def validate_device_id(self, value):
        return normalize_device_id(value)

    def validate_locations(self, value):
        if not value:
            raise serializers.ValidationError("locations는 최소 1개 이상이어야 합니다.")
        return value


class GeoDataIngestResponseSerializer(serializers.Serializer):
    status = serializers.CharField()
    saved_count = serializers.IntegerField()