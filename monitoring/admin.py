from django.contrib import admin
from .models import Protectee, Event


@admin.register(Protectee)
class ProtecteeAdmin(admin.ModelAdmin):
    list_display = ('id', 'name', 'device_id')
    search_fields = ('name',)


@admin.register(Event)
class EventAdmin(admin.ModelAdmin):
    list_display = ('id', 'protectee', 'timestamp', 'event_type', 'imu_danger_level', 'ppg_holding')
    list_filter = ('event_type', 'protectee')
    search_fields = ('protectee__name',)
