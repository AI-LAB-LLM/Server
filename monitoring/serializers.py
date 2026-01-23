from rest_framework import serializers


# IMU팀이 워치/앱에서 서버로 보낼 JSON 형식
class IMUAlertSerializer(serializers.Serializer):
    device_id = serializers.CharField(max_length=100)
    timestamp = serializers.DateTimeField()
    imu_danger_level = serializers.IntegerField()

class GEOAlertSerializer(serializers.Serializer):
    device_id = serializers.CharField(max_length=100)
    timestamp = serializers.DateTimeField()
    latitude = serializers.FloatField()
    longitude = serializers.FloatField()