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


class GeoDataIngestView(APIView):
    """
    POST /api/geo/data
    """

    @extend_schema(
        request=GeoDataIngestSerializer,
        responses={201: GeoDataIngestResponseSerializer},
        summary="GEO 위치 데이터 수신",
        description=(
            "위치 데이터를 배열 형태로 수신합니다.\n\n"
            "필드 설명:\n"
            "- device_id (string): 워치 고유 ID\n"
            "- locations (array): 위치 데이터 배열\n\n"
            "locations 내부 필드:\n"
            "- timestamp (long, UNIX ms)\n"
            "- pos_success (boolean): 위치 수신 성공 여부\n"
            "- pos_info (object, optional): pos_success=true일 때만 포함\n\n"
            "pos_info 내부 필드:\n"
            "- longitude (double): 경도\n"
            "- latitude (double): 위도\n"
            "- accuracy_h (double): 정확도\n\n"
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