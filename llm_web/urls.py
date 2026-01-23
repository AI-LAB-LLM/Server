from django.contrib import admin
from django.urls import path, include
from ppg.views_api import IngestView, RecordsView, BaselineSessionView
from monitoring.views import (
    HealthCheckView,
    IMUAlertView,
    GEOAlertView, 
)
from drf_spectacular.views import (
    SpectacularAPIView,
    SpectacularSwaggerView,
)

urlpatterns = [
    path("admin/", admin.site.urls),

    path("", include("home.urls")),
    path("dbchat/", include("dbchat.urls")),
    path("report/", include("report.urls", namespace="report")),
    path("ppg/", include("ppg.urls")),

    # =========================
    # Sensor APIs
    # =========================
    path("api/v1/health", HealthCheckView.as_view()),
    path("api/v1/events/imu-alert", IMUAlertView.as_view()),
    path("api/v1/events/geo-alert", GEOAlertView.as_view()), 

    # =========================
    # Swagger / OpenAPI
    # =========================
    path("api/schema/", SpectacularAPIView.as_view(), name="schema"),
    path(
        "api/docs/",
        SpectacularSwaggerView.as_view(url_name="schema"),
        name="swagger-ui",
    ),

     # ---- API (DRF) ----
    path("api/ingest/",  IngestView.as_view(),  name="api-ingest"),
    path("api/records/", RecordsView.as_view(), name="api-records"),
    path('api/baseline/', BaselineSessionView.as_view(), name='baseline-session'),

   
]

