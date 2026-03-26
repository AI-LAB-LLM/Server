from django.contrib import admin
from .models import GeoData


@admin.register(GeoData)
class GeoDataAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "device_id",
        "protectee",
        "timestamp",
        "pos_success",
        "latitude",
        "longitude",
        "accuracy_h",
    )
    list_filter = ("pos_success",)
    search_fields = ("device_id", "protectee__name", "protectee__device_id")
    ordering = ("-timestamp",)