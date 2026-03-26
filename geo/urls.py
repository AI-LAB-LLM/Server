from django.urls import path
from .views import GeoDataIngestView

urlpatterns = [
    path("data", GeoDataIngestView.as_view(), name="geo-data-ingest"),
]