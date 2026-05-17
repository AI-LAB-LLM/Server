from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status, serializers
from drf_spectacular.utils import extend_schema, OpenApiExample, inline_serializer

from monitoring.models import Event
from .serializers import ImuDataIngestSerializer
from .calculator import calculate_imu_level


class ImuDataIngestView(APIView):
    """
    POST /api/v1/events/imu-raw
    """

    @extend_schema(
        request=ImuDataIngestSerializer,
        responses={
            201: inline_serializer(
                name="ImuDataAlgorithmResponse",
                fields={
                    "id": serializers.IntegerField(),
                    "protectee_id": serializers.IntegerField(),
                    "window_index": serializers.IntegerField(),
                    "sample_rate": serializers.IntegerField(),
                    "window_sec": serializers.IntegerField(),
                    "sample_count": serializers.IntegerField(),
                    "imu_level": serializers.IntegerField(),
                    "features": serializers.DictField(),
                    "probs": serializers.ListField(
                        child=serializers.FloatField()
                    ),
                    "event_id": serializers.IntegerField(),
                },
            )
        },
        summary="IMU 12초 window raw 데이터 저장 및 level 계산",
        description=(
            "Galaxy Watch에서 전송한 IMU 12초 window 데이터를 저장합니다.\n\n"
            "현재 서버 입력 기준은 다음과 같습니다.\n"
            "- sample_rate: 25Hz\n"
            "- window_sec: 12초\n"
            "- samples: 300개\n"
            "- samples 형태: [[x, y, z], ...]\n\n"
            "저장 직후 서버에서 IMU level 1~5를 계산하고, "
            "monitoring_event 테이블에 IMU_ALERT 이벤트로 저장합니다.\n\n"
            "응답에는 raw samples 전체를 다시 내려주지 않고, "
            "Logcat에서 확인하기 쉽도록 imu_level, features, probs만 내려줍니다."
        ),
        examples=[
            OpenApiExample(
                "IMU 12초 window 예시",
                value={
                    "device_id": "SM-L300_19395f6a434f4ca6",
                    "window_index": 18,
                    "start_timestamp": "2026-05-15T11:03:31.918Z",
                    "end_timestamp": "2026-05-15T11:03:43.896Z",
                    "sample_rate": 25,
                    "window_sec": 12,
                    "samples": [
                        [3.9947, 0.5665, 8.7086],
                        [4.0207, 0.4761, 8.7418],
                        [3.9695, 0.5415, 8.7485],
                    ],
                },
                request_only=True,
            ),
            OpenApiExample(
                "IMU 알고리즘 응답 예시",
                value={
                    "id": 188,
                    "protectee_id": 2,
                    "window_index": 18,
                    "sample_rate": 25,
                    "window_sec": 12,
                    "sample_count": 300,
                    "imu_level": 1,
                    "features": {
                        "svm_mean": 9.81,
                        "svm_std": 0.42,
                        "d_svm_mean": 0.13,
                        "d_svm_std": 0.08,
                    },
                    "probs": [0.8, 0.1, 0.05, 0.03, 0.02],
                    "event_id": 10,
                },
                response_only=True,
            ),
        ],
    )
    def post(self, request):
        serializer = ImuDataIngestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        # 1) IMU raw data DB 저장
        imu_data = serializer.save()

        # 2) IMU level 계산
        result = calculate_imu_level(
            protectee_id=imu_data.protectee_id,
            samples=imu_data.samples,
        )

        level = result["level"]

        # 3) monitoring_event에 IMU_ALERT 저장
        event = Event.objects.create(
            protectee=imu_data.protectee,
            timestamp=imu_data.end_timestamp,
            event_type=Event.EventType.IMU_ALERT,
            imu_danger_level=level,
        )

        # 4) Logcat에서 보기 쉽게 짧은 응답만 반환
        data = {
            "id": imu_data.id,
            "protectee_id": imu_data.protectee_id,
            "window_index": imu_data.window_index,
            "sample_rate": imu_data.sample_rate,
            "window_sec": imu_data.window_sec,
            "sample_count": len(imu_data.samples),
            "imu_level": level,
            "features": result.get("features"),
            "probs": result.get("probs"),
            "event_id": event.id,
        }

        return Response(data, status=status.HTTP_201_CREATED)