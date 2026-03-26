from datetime import datetime, timezone
from rest_framework import serializers


class UnixMsDateTimeField(serializers.Field):
    """
    입력: UNIX time in milliseconds (long)
    예: 1754947824901

    내부: timezone-aware UTC datetime
    """

    default_error_messages = {
        "invalid": "timestamp는 UNIX time(ms) 형식의 정수여야 합니다."
    }

    def to_internal_value(self, value):
        try:
            ts_ms = int(value)
            ts_sec = ts_ms / 1000.0
            return datetime.fromtimestamp(ts_sec, tz=timezone.utc)
        except (TypeError, ValueError, OSError, OverflowError):
            self.fail("invalid")

    def to_representation(self, value):
        # 응답에서 다시 ms로 보여주고 싶으면 이렇게 유지
        return int(value.timestamp() * 1000)