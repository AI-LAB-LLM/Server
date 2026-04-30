from django.contrib import admin
from .models import ImuData


@admin.register(ImuData)
class ImuDataAdmin(admin.ModelAdmin):
    list_display = [
        "id",
        "protectee",
        "window_index",
        "start_timestamp",
        "end_timestamp",
        "sample_rate",
        "window_sec",
        "created_at",
    ]
    list_filter = ["protectee", "created_at"]
    search_fields = ["protectee__name", "protectee__device_id"]