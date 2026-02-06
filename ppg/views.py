# core/views.py
from django.shortcuts import render, get_object_or_404
from .models import SensorData
from .utils import serialize_sensor_row
from django.views.decorators.clickjacking import xframe_options_sameorigin
from django.utils.decorators import method_decorator
from monitoring.models import Event, Protectee 
from django.conf import settings


def dashboard_view(request):
    rows = SensorData.objects.order_by("-timestamp")[:20]
    items = [serialize_sensor_row(r) for r in rows][::-1]
    return render(request, "dashboard.html", {"items": items})

def dashboard_device_view(request, device_id: str):
    protectee = get_object_or_404(Protectee, device_id=device_id)

    rows = SensorData.objects.filter(device_id=device_id).order_by("-id")[:120]
    items = [serialize_sensor_row(r) for r in rows][::-1]

    return render(
        request,
        "dashboard_device.html",
        {
            "items": items,
            "device_id": device_id,
            "protectee_name": protectee.name,
            "KAKAO_JS_KEY": settings.KAKAO_JS_KEY,
        },
    )