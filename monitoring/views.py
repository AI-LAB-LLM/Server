from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from django.shortcuts import get_object_or_404
from drf_spectacular.utils import extend_schema
from monitoring.models import Protectee, Event
from monitoring.serializers import (
    IMUAlertSerializer,
    GEOAlertSerializer,
)


class HealthCheckView(APIView):
    """
    서버 상태 확인용
    GET /api/v1/health
    """
    def get(self, request):
        return Response({"status": "ok"}, status=status.HTTP_200_OK)



# IMU Alert API
@extend_schema(
    request=IMUAlertSerializer,
    responses={201: None},
    summary="IMU 위험 알람 수신",
    description=(
        "IMU 센서에서 위험도가 특정 임계치를 넘었을 때 호출되는 API\n"
        "- protectee_id: 보호대상자 ID (Protectee.pk)\n"
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

        # external_id 필드가 없으므로 pk(id)로 조회
        protectee = get_object_or_404(Protectee, pk=data["protectee_id"])

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
            },
            status=status.HTTP_201_CREATED,
        )



# GEO Alert API
@extend_schema(
    request=GEOAlertSerializer,
    responses={201: None},
    summary="GEO 위치 이벤트 수신",
    description=(
        "실시간 위치 정보 및 평소 경로 여부를 수신하는 API\n"
        "- protectee_id: 보호대상자 ID (Protectee.pk)\n"
        "- is_usual_route: 1=평소 경로, 0=이탈"
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

        # external_id 필드가 없으므로 pk(id)로 조회
        protectee = get_object_or_404(Protectee, pk=data["protectee_id"])

        event = Event.objects.create(
            protectee=protectee,
            timestamp=data["timestamp"],
            event_type=Event.EventType.GEO_ALERT,
            latitude=data["latitude"],
            longitude=data["longitude"],
            is_usual_route=data["is_usual_route"],
        )

        return Response(
            {
                "status": "ok",
                "event_id": event.id,
            },
            status=status.HTTP_201_CREATED,
        )
