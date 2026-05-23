from django.contrib import admin
from django.urls import path, include
from ppg.views_api import (
    IngestView,
    RecordsView,
    BaselineSessionView,
    EventStatusView,
)
from monitoring.views import IMUAlertView
from geo.views import GEOAlertView
from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView


urlpatterns = [
    path("admin/", admin.site.urls),

    path("", include("home.urls")),
    path("dbchat/", include("dbchat.urls")),
    path("report/", include("report.urls", namespace="report")),
    path("ppg/", include("ppg.urls")),
    path("apnea/", include("apnea.urls")),

    # =========================
    # Sensor APIs
    # =========================
    path("api/v1/events/imu-alert", IMUAlertView.as_view()),
    path("api/v1/events/geo-alert", GEOAlertView.as_view()),
    path("api/v1/geo/", include("geo.urls")),
    path("api/v1/events/", include("imu.urls")),

    # =========================
    # Swagger / OpenAPI (기존)
    # =========================
    path("api/schema/", SpectacularAPIView.as_view(), name="schema"),
    path(
        "api/docs/",
        SpectacularSwaggerView.as_view(url_name="schema"),
        name="swagger-ui",
    ),

    # =========================
    # Swagger / OpenAPI (GEO 전용)
    # =========================
    path(
        "api/geo/schema/",
        SpectacularAPIView.as_view(
            urlconf="geo.urls",
            custom_settings={
                "TITLE": "GEO Data API",
                "DESCRIPTION": "GEO 위치 데이터 송수신 전용 문서",
                "VERSION": "1.0.0",
            },
        ),
        name="geo-schema",
    ),
    path(
        "api/geo/docs/",
        SpectacularSwaggerView.as_view(url_name="geo-schema"),
        name="geo-swagger-ui",
    ),

    # ---- API (DRF) ----
    path("api/ingest/", IngestView.as_view(), name="api-ingest"),
    path("api/records/", RecordsView.as_view(), name="api-records"),
    path("api/baseline/", BaselineSessionView.as_view(), name="baseline-session"),
    path("api/event_status/", EventStatusView.as_view(), name="api_event_status"),
]