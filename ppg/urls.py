from django.urls import path
from ppg import views

app_name = 'ppg'

urlpatterns = [
    path('dashboard/', views.dashboard_view, name='dashboard'),
    path("dashboard/device/<str:device_id>/", views.dashboard_device_view, name="dashboard_device"),

]
