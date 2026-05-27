from django.shortcuts import render
from monitoring.models import Protectee
from apnea.models import ApneaChunk
from datetime import timedelta
from django.utils import timezone


def home(request):
    protectees = Protectee.objects.all().order_by("id")

    now = timezone.now()
    fresh_window = now - timedelta(minutes=2)

    users = []
    for p in protectees:
        latest = (
            ApneaChunk.objects
            .filter(device_id=p.device_id, timestamp__range=(fresh_window, now))
            .order_by("-timestamp")
            .first()
        )

        wear_label = latest.wear_label if latest else None
        status = "활동중" if wear_label == 1 else "비활동중"

        users.append({
            "name":      p.name,
            "gender":    p.gender,
            "age":       p.age,
            "address":   p.address,
            "phone":     p.phone,
            "device_id": p.device_id,
            "status":    status,
        })

    return render(request, "home/home.html", {"users": users})