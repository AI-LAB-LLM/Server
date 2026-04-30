from django.urls import path
from .views import ImuDataIngestView

urlpatterns = [
    path("imu-raw", ImuDataIngestView.as_view(), name="imu-raw"),
]