from datetime import timezone

from rest_framework import serializers
from monitoring.models import Protectee
from monitoring.utils import normalize_device_id
from .models import ImuData


def to_utc_datetime(dt):
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)

    return dt.astimezone(timezone.utc)


class ImuDataIngestSerializer(serializers.Serializer):
    device_id = serializers.CharField(max_length=100)
    window_index = serializers.IntegerField()
    start_timestamp = serializers.DateTimeField()
    end_timestamp = serializers.DateTimeField()
    sample_rate = serializers.IntegerField(default=50)
    window_sec = serializers.IntegerField(default=6)

    # samples = [[x, y, z], [x, y, z], ...]
    samples = serializers.ListField(
        child=serializers.ListField(
            child=serializers.FloatField(),
            min_length=3,
            max_length=3,
        ),
        min_length=300,
        max_length=300,
    )

    def validate_device_id(self, value):
        return normalize_device_id(value)

    def validate_samples(self, value):
        if len(value) != 300:
            raise serializers.ValidationError(
                f"IMU samples는 반드시 300개여야 합니다. 현재 {len(value)}개입니다."
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