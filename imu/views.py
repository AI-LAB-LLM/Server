from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from drf_spectacular.utils import extend_schema, OpenApiExample
from monitoring.models import Event
from .serializers import ImuDataIngestSerializer, ImuDataResponseSerializer
from .calculator import calculate_imu_level


class ImuDataIngestView(APIView):
    """
    POST /api/v1/events/imu-raw
    """

    @extend_schema(
        request=ImuDataIngestSerializer,
        responses={201: ImuDataResponseSerializer},
        summary="IMU 6초 window raw 데이터 저장 및 level 계산",
        description=(
            "Galaxy Watch에서 전송한 IMU 6초 window 데이터를 저장합니다.\n"
            "samples는 [[x, y, z], ...] 형태입니다.\n"
            "저장 직후 서버에서 IMU level 1~5를 계산하고, "
            "monitoring_event 테이블에 IMU_ALERT 이벤트로 저장합니다."
        ),
        examples=[
            OpenApiExample(
                "IMU 6초 window 예시",
                value={
                    "device_id": "SM-L320_0a3cecbde642c88d",
                    "window_index": 1,
                    "start_timestamp": "2026-04-30T06:35:13.885Z",
                    "end_timestamp": "2026-04-30T06:35:19.865Z",
                    "sample_rate": 50,
                    "window_sec": 6,
                    "samples": [
                        [0.0077, 5.7919, 7.7678],
                        [0.0811, 5.8486, 7.6947],
                        [0.0811, 5.8465, 7.6881],
                    ],
                },
                request_only=True,
            )
        ],
    )
    def post(self, request):
        serializer = ImuDataIngestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        imu_data = serializer.save()

        # 1) IMU level 계산
        result = calculate_imu_level(
            protectee_id=imu_data.protectee_id,
            samples=imu_data.samples,
        )
        level = result["level"]

        # 2) monitoring_event에 IMU_ALERT 저장
        event = Event.objects.create(
            protectee=imu_data.protectee,
            timestamp=imu_data.end_timestamp,
            event_type=Event.EventType.IMU_ALERT,
            imu_danger_level=level,
        )

        # 3) 응답
        data = ImuDataResponseSerializer(imu_data).data
        data["imu_level"] = level
        data["event_id"] = event.id
        data["features"] = result["features"]
        data["probs"] = result["probs"]

        return Response(data, status=status.HTTP_201_CREATED)