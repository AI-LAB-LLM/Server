from rest_framework.authentication import BaseAuthentication
from rest_framework import exceptions
from users.models import WatchDevice

class DeviceIDHeaderAuth(BaseAuthentication):
    """요청 헤더 X-Device-Id 또는 body.device_id 로 디바이스 인증
    사전 등록된 디바이스만 허용, 해당 디바이스의 owner를 request.user 로 설정
    """
    def authenticate(self, request):
        device_id = request.headers.get("X-Device-Id")
        if not device_id and hasattr(request, "data"):
            device_id = request.data.get("device_id")
        if not device_id:
            return None  # 다른 인증으로 넘어감

        try:
            device = WatchDevice.objects.select_related("owner").get(device_id=device_id, is_active=True)
        except WatchDevice.DoesNotExist:
            raise exceptions.AuthenticationFailed("Unknown or inactive device")
        return (device.owner, None)