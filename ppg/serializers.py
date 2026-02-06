from rest_framework import serializers

# Ingest (요청)
class IngestRequestSerializer(serializers.Serializer):
    device_id = serializers.CharField(max_length=64)
    timestamp = serializers.CharField(help_text="ISO8601 datetime (e.g. 2025-10-13T12:34:56.000Z)")
    ppg_green = serializers.ListField(child=serializers.FloatField())
    ppg_ir    = serializers.ListField(child=serializers.FloatField(), allow_empty=False)
    ppg_red   = serializers.ListField(child=serializers.FloatField(), allow_empty=False)

# Ingest (응답)
class IngestResponseSerializer(serializers.Serializer):
    ok          = serializers.BooleanField()
    id          = serializers.IntegerField()
    predictions = serializers.DictField()

# Records (응답 아이템)
class RecordItemSerializer(serializers.Serializer):
    device_id = serializers.CharField()
    timestamp = serializers.CharField()
    ppg_green = serializers.ListField(child=serializers.FloatField())
    ppg_ir    = serializers.ListField(child=serializers.FloatField())
    ppg_red   = serializers.ListField(child=serializers.FloatField(), required=False)
    predictions  = serializers.DictField(required=False)

# Records (응답 컨테이너)
class RecordsResponseSerializer(serializers.Serializer):
    ok    = serializers.BooleanField()
    items = RecordItemSerializer(many=True)
    total = serializers.IntegerField(required=False)
