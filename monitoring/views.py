from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from drf_spectacular.utils import extend_schema

from monitoring.models import Protectee, Event
from monitoring.serializers import IMUAlertSerializer, GEOAlertSerializer


class HealthCheckView(APIView):
    """
    서버 상태 확인용
    GET /api/v1/health
    """
    def get(self, request):
        return Response({"status": "ok"}, status=status.HTTP_200_OK)


# =========================
# IMU Alert API
# =========================
@extend_schema(
    request=IMUAlertSerializer,
    responses={201: None},
    summary="IMU 위험 알람 수신",
    description=(
        "IMU 센서에서 위험도가 특정 임계치를 넘었을 때 호출되는 API\n"
        "- device_id: 워치 고유 ID (Protectee.device_id)\n"
        "- timestamp: 이벤트 발생 시각\n"
        "- imu_danger_level: 위험도 (정수)"
    ),
)
class IMUAlertView(APIView):
    """
    POST /api/v1/events/imu-alert
    """
    def post(self, request):
        serializer = IMUAlertSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        device_id = data["device_id"]

        # ✅ 없으면 Protectee 자동 생성
        protectee, created = Protectee.objects.get_or_create(
            device_id=device_id,
            defaults={"name": f"unknown-{device_id[:6]}"},
        )

        event = Event.objects.create(
            protectee=protectee,
            timestamp=data["timestamp"],
            event_type=Event.EventType.IMU_ALERT,
            imu_danger_level=data["imu_danger_level"],
        )

        return Response(
            {
                "status": "ok",
                "event_id": event.id,
                "protectee_id": protectee.id,
                "protectee_created": created,
            },
            status=status.HTTP_201_CREATED,
        )


# =========================
# GEO Alert API
# =========================
@extend_schema(
    request=GEOAlertSerializer,
    responses={201: None},
    summary="GEO 위치 이벤트 수신",
    description=(
        "실시간 위치 정보를 수신하는 API\n"
        "- device_id: 워치 고유 ID (Protectee.device_id)\n"
        "- timestamp: 이벤트 발생 시각\n"
        "- latitude, longitude: 위도/경도"
    ),
)
class GEOAlertView(APIView):
    """
    POST /api/v1/events/geo-alert
    """
    def post(self, request):
        serializer = GEOAlertSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        device_id = data["device_id"]

        # ✅ 없으면 Protectee 자동 생성 (IMU와 동일)
        protectee, created = Protectee.objects.get_or_create(
            device_id=device_id,
            defaults={"name": f"unknown-{device_id[:6]}"},
        )

        event = Event.objects.create(
            protectee=protectee,
            timestamp=data["timestamp"],
            event_type=Event.EventType.GEO_ALERT,
            latitude=data["latitude"],
            longitude=data["longitude"],
        )

        return Response(
            {
                "status": "ok",
                "event_id": event.id,
                "protectee_id": protectee.id,
                "protectee_created": created,
            },
            status=status.HTTP_201_CREATED,
        )
