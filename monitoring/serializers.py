from rest_framework import serializers


# IMU팀이 워치/앱에서 서버로 보낼 JSON 형식
class IMUAlertSerializer(serializers.Serializer):
    protectee_id = serializers.CharField()
    timestamp = serializers.DateTimeField()
    imu_danger_level = serializers.IntegerField()

class GEOAlertSerializer(serializers.Serializer):
    protectee_id = serializers.CharField()
    timestamp = serializers.DateTimeField()
    latitude = serializers.FloatField()
    longitude = serializers.FloatField()
    is_usual_route = serializers.IntegerField(min_value=0, max_value=1)