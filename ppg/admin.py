from django.contrib import admin
from .models import SensorData

@admin.register(SensorData)
class SensorDataAdmin(admin.ModelAdmin):
    list_display = ("device_id", "timestamp", "created_at")
    list_filter = ("device_id",)
    search_fields = ("device_id",)