from drf_spectacular.utils import extend_schema, OpenApiExample
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from monitoring.models import Protectee
from .models import GeoData
from .serializers import (
    GeoDataIngestSerializer,
    GeoDataIngestResponseSerializer,
)
from monitoring.serializers import GEOAlertSerializer
from .gpr_services import create_geo_processed_data_and_run_gpr


class GeoDataIngestView(APIView):
    """
    POST /api/geo/data
    """

    @extend_schema(
        request=GeoDataIngestSerializer,
        responses={201: GeoDataIngestResponseSerializer},
        summary="GEO 위치 데이터 수신",
        description=(
            "실시간 위치 정보를 수신하는 API입니다.\n\n"
            "기존에는 monitoring_event 테이블에 latitude, longitude를 저장했지만, "
            "이제는 geo_processed_data 테이블에 저장합니다.\n\n"
            "- raw_latitude/raw_longitude: 원본 GPS\n"
            "- latitude/longitude: GPRRuntime 처리 후 지도 표시용 최종 좌표\n"
            "- 최초 저장 시 latitude/longitude는 null로 생성됩니다.\n"
            "- 이후 GPRRuntime 처리 결과에 따라 latitude/longitude가 업데이트됩니다.\n"
            "- 정상 GPS로 판단되면 최종 좌표가 raw 좌표와 동일할 수 있습니다."
        ),
        examples=[
            OpenApiExample(
                name="성공 케이스",
                value={
                    "device_id": "5456a4dfb33d71d5",
                    "locations": [
                        {
                            "timestamp": 1672531200000,
                            "pos_success": True,
                            "pos_info": {
                                "longitude": 126.9780,
                                "latitude": 37.5665,
                                "accuracy_h": 5.5
                            }
                        }
                    ]
                },
                request_only=True,
            ),
            OpenApiExample(
                name="실패 케이스",
                value={
                    "device_id": "5456a4dfb33d71d5",
                    "locations": [
                        {
                            "timestamp": 1672531200000,
                            "pos_success": False
                        }
                    ]
                },
                request_only=True,
            ),
        ],
    )
    def post(self, request):
        serializer = GeoDataIngestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        device_id = data["device_id"]

        protectee, created = Protectee.objects.get_or_create(
            device_id=device_id,
            defaults={"name": f"unknown-{device_id[:6]}"},
        )

        created_rows = []

        for item in data["locations"]:
            pos_success = item["pos_success"]
            pos_info = item.get("pos_info")

            geo_row = GeoData.objects.create(
                protectee=protectee,
                device_id=device_id,
                timestamp=item["timestamp"],  # 이미 UTC datetime 객체
                pos_success=pos_success,
                longitude=pos_info["longitude"] if pos_success and pos_info else None,
                latitude=pos_info["latitude"] if pos_success and pos_info else None,
                accuracy_h=pos_info["accuracy_h"] if pos_success and pos_info else None,
            )
            created_rows.append(geo_row.id)

        response_data = {
            "status": "ok",
            "saved_count": len(created_rows),
        }

        return Response(response_data, status=status.HTTP_201_CREATED)
    


# GEO Alert API - 기존 monitoring_event 저장 대신 geo_processed_data 저장
@extend_schema(
    request=GEOAlertSerializer,
    responses={201: None},
    summary="GEO 위치 이벤트 수신, GPR 보정 및 경로 이상탐지",
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

        protectee, created = Protectee.objects.get_or_create(
            device_id=device_id,
            defaults={"name": f"unknown-{device_id[:6]}"},
        )

        latitude = data.get("latitude")
        longitude = data.get("longitude")

        geo_row, gpr_result, anomaly_result = create_geo_processed_data_and_run_gpr(
            protectee=protectee,
            device_id=device_id,
            timestamp=data["timestamp"],
            latitude=latitude,
            longitude=longitude,
        )

        return Response(
            {
                "status": "ok",
                "geo_processed_id": geo_row.id,
                "protectee_id": protectee.id,
                "protectee_created": created,
                "device_id": geo_row.device_id,
                "timestamp": geo_row.timestamp,
                "raw_latitude": geo_row.raw_latitude,
                "raw_longitude": geo_row.raw_longitude,
                "latitude": geo_row.latitude,
                "longitude": geo_row.longitude,
                "pos_success": geo_row.pos_success,
                "gpr": gpr_result,
                "anomaly": anomaly_result,
                "map_notice": "지도에는 latitude / longitude를 사용하세요.",
            },
            status=status.HTTP_201_CREATED,
        )