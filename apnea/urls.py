from django.urls import path
from . import views
from .views import SosStatusView, SosCloseView

app_name = "apnea"

urlpatterns = [
    path("",                views.DashboardView.as_view(),    name="dashboard"),
    path("device/<str:device_id>/", views.DeviceDashboardView.as_view(),  name="device_dashboard"),
    path("api/ingest/",     views.IngestView.as_view(),       name="ingest"),
    path("api/records/",    views.RecordsView.as_view(),      name="records"),
    path("api/baseline/",   views.BaselineStartView.as_view(),name="baseline_start"),
    path("api/status/",     views.ModelStatusView.as_view(),  name="model_status"),
    path("api/event_status/", views.EventStatusView.as_view(), name="event_status"),
    path("api/baselines/", views.BaselineListView.as_view(),name="baseline_list",),
    path("api/baselines/select/",views.BaselineSelectView.as_view(),name="baseline_select",),
    path("api/sos/status/",SosStatusView.as_view(),name="sos_status",),
    path("api/sos/close/",SosCloseView.as_view(),name="sos_close",),

]