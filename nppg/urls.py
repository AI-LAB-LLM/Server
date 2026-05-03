from django.urls import path
from . import views

app_name = "nppg"

urlpatterns = [
    # 대시보드 HTML 페이지
    path("dashboard/",                        views.dashboard_view,        name="dashboard"),
    path("dashboard/device/<str:device_id>/", views.dashboard_device_view, name="dashboard_device"),

    # API
    path("api/records/",      views.RecordsView.as_view(),    name="records"),
    path("api/baseline/",     views.BaselineView.as_view(),   name="baseline"),
    path("api/event-status/", views.EventStatusView.as_view(), name="event_status"),
]