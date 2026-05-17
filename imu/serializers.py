from datetime import timezone
from rest_framework import serializers
from monitoring.models import Protectee
from monitoring.utils import normalize_device_id
from .models import ImuData


EXPECTED_SAMPLE_RATE = 25
EXPECTED_WINDOW_SEC = 12
EXPECTED_SAMPLE_COUNT = EXPECTED_SAMPLE_RATE * EXPECTED_WINDOW_SEC  # 300


def to_utc_datetime(dt):
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)

    return dt.astimezone(timezone.utc)


class ImuDataIngestSerializer(serializers.Serializer):
    device_id = serializers.CharField(max_length=100)
    window_index = serializers.IntegerField()
    start_timestamp = serializers.DateTimeField()
    end_timestamp = serializers.DateTimeField()

    # 기본값 25Hz, 12초
    sample_rate = serializers.IntegerField(default=EXPECTED_SAMPLE_RATE)
    window_sec = serializers.IntegerField(default=EXPECTED_WINDOW_SEC)

    # samples = [[x, y, z], [x, y, z], ...]
    # 25Hz * 12초 = 300개
    samples = serializers.ListField(
        child=serializers.ListField(
            child=serializers.FloatField(),
            min_length=3,
            max_length=3,
        ),
        min_length=EXPECTED_SAMPLE_COUNT,
        max_length=EXPECTED_SAMPLE_COUNT,
    )

    def validate_device_id(self, value):
        return normalize_device_id(value)

    def validate(self, attrs):
        sample_rate = attrs.get("sample_rate", EXPECTED_SAMPLE_RATE)
        window_sec = attrs.get("window_sec", EXPECTED_WINDOW_SEC)
        samples = attrs.get("samples", [])

        if sample_rate != EXPECTED_SAMPLE_RATE:
            raise serializers.ValidationError({
                "sample_rate": (
                    f"IMU sample_rate는 반드시 {EXPECTED_SAMPLE_RATE}Hz여야 합니다. "
                    f"현재 {sample_rate}Hz입니다."
                )
            })

        if window_sec != EXPECTED_WINDOW_SEC:
            raise serializers.ValidationError({
                "window_sec": (
                    f"IMU window_sec는 반드시 {EXPECTED_WINDOW_SEC}초여야 합니다. "
                    f"현재 {window_sec}초입니다."
                )
            })

        expected_count = sample_rate * window_sec

        if len(samples) != expected_count:
            raise serializers.ValidationError({
                "samples": (
                    f"IMU samples는 반드시 {expected_count}개여야 합니다. "
                    f"현재 {len(samples)}개입니다. "
                    f"기준: {sample_rate}Hz * {window_sec}초"
                )
            })

        start_timestamp = attrs.get("start_timestamp")
        end_timestamp = attrs.get("end_timestamp")

        if start_timestamp and end_timestamp:
            start_utc = to_utc_datetime(start_timestamp)
            end_utc = to_utc_datetime(end_timestamp)

            if end_utc <= start_utc:
                raise serializers.ValidationError({
                    "end_timestamp": "end_timestamp는 start_timestamp보다 뒤 시간이어야 합니다."
                })

        return attrs

    def validate_samples(self, value):
        if len(value) != EXPECTED_SAMPLE_COUNT:
            raise serializers.ValidationError(
                f"IMU samples는 반드시 {EXPECTED_SAMPLE_COUNT}개여야 합니다. "
                f"현재 {len(value)}개입니다."
            )

        for i, sample in enumerate(value):
            if len(sample) != 3:
                raise serializers.ValidationError(
                    f"samples[{i}]는 [x, y, z] 형태의 길이 3 배열이어야 합니다."
                )

        return value

    def create(self, validated_data):
        device_id = validated_data.pop("device_id")

        protectee, created = Protectee.objects.get_or_create(
            device_id=device_id,
            defaults={"name": f"unknown-{device_id[:6]}"},
        )

        imu_data = ImuData.objects.create(
            protectee=protectee,
            window_index=validated_data["window_index"],
            start_timestamp=to_utc_datetime(validated_data["start_timestamp"]),
            end_timestamp=to_utc_datetime(validated_data["end_timestamp"]),
            sample_rate=validated_data["sample_rate"],
            window_sec=validated_data["window_sec"],
            samples=validated_data["samples"],
        )

        return imu_data


class ImuDataResponseSerializer(serializers.ModelSerializer):
    protectee_id = serializers.IntegerField(source="protectee.id", read_only=True)

    class Meta:
        model = ImuData
        fields = [
            "id",
            "protectee_id",
            "window_index",
            "start_timestamp",
            "end_timestamp",
            "sample_rate",
            "window_sec",
            "samples",
            "created_at",
        ]