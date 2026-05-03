"""
nppg 앱의 뷰 모음.

핵심 로직:
  - ppg.SensorData를 읽기+쓰기로 사용합니다 (별도 테이블 없음).
  - predictions["IR_HOLDING"]["model"] == "nppg" 이면 이미 새 모델 결과이므로
    추론을 스킵합니다.
  - 아직 저장 안 된 레코드만 추론 후 predictions["IR_HOLDING"]을 덮어쓰고
    DB에 update합니다. 나머지 키(WEAR_GREEN, R_RATIO 등)는 건드리지 않습니다.
"""

from django.shortcuts import render, get_object_or_404
from django.conf import settings
from rest_framework.views import APIView
from rest_framework.response import Response
from django.views.decorators.csrf import csrf_exempt
from django.utils.decorators import method_decorator
from django.utils import timezone
from datetime import timedelta
from zoneinfo import ZoneInfo

from ppg.models import SensorData       # 기존 테이블 그대로 사용
from monitoring.models import Protectee, Event
from . import model_n_runtime

KST = ZoneInfo("Asia/Seoul")


# ── 대시보드 HTML 뷰 ──────────────────────────────────────────────────────────

def dashboard_view(request):
    """전체 대시보드 — 초기 렌더링용으로 최근 20개를 넘깁니다."""
    rows  = SensorData.objects.order_by("-timestamp")[:20]
    items = [_serialize(r) for r in rows][::-1]
    return render(request, "nppg/dashboard.html", {"items": items})


def dashboard_device_view(request, device_id: str):
    """디바이스별 대시보드 — 해당 device_id의 최근 120개를 넘깁니다."""
    protectee = get_object_or_404(Protectee, device_id=device_id)
    rows  = SensorData.objects.filter(device_id=device_id).order_by("-id")[:120]
    items = [_serialize(r) for r in rows][::-1]
    return render(
        request,
        "nppg/dashboard_device.html",
        {
            "items":          items,
            "device_id":      device_id,
            "protectee_name": protectee.name,
            "KAKAO_JS_KEY":   settings.KAKAO_JS_KEY,
        },
    )


# ── API 뷰 ────────────────────────────────────────────────────────────────────

@method_decorator(csrf_exempt, name="dispatch")
class RecordsView(APIView):
    """
    DB에서 읽기만 합니다 (기존 ppg RecordsView와 동일한 속도).
    
    - WEAR_GREEN : ppg ingest가 저장한 값 그대로 반환 → 착용 카드
    - R_RATIO    : ppg ingest가 저장한 값 그대로 반환 → R ratio 차트
    - IR_HOLDING : ppg ingest가 저장한 기존 모델 결과를
                   새 모델(nppg) 결과로 교체해서 반환
                   단, 새 모델 결과가 이미 저장되어 있으면 (model=="nppg") 스킵
    """
    authentication_classes = []
    MAX_ITEMS = 120

    def get(self, request):
        try:
            limit = int(request.GET.get("limit", 20))
        except ValueError:
            limit = 20
        limit = max(1, min(limit, self.MAX_ITEMS))

        qs = SensorData.objects.all()

        device_id = request.GET.get("device_id")
        if device_id:
            qs = qs.filter(device_id=device_id)

        minutes = request.GET.get("minutes")
        if minutes:
            try:
                since = timezone.now() - timedelta(minutes=int(minutes))
                qs = qs.filter(timestamp__gte=since)
            except ValueError:
                pass

        rows  = list(qs.order_by("-id")[:limit])
        items = []

        for r in reversed(rows):
            preds = dict(r.predictions or {})
            ir    = preds.get("IR_HOLDING") or {}

            # ── IR_HOLDING만 새 모델로 교체 ───────────────────────────
            # 이미 새 모델 결과면 스킵 (중복 추론 방지)
            if not (ir.get("model") == "nppg" and ir.get("valid") == True) and r.ppg_green:
                result = model_n_runtime.run_inference(r.device_id, r.ppg_green)
                preds["IR_HOLDING"] = result
                # valid한 결과만 DB에 저장합니다.
                # collecting_baseline 등 임시 상태는 저장하지 않습니다.
                if result.get("valid"):
                    SensorData.objects.filter(pk=r.pk).update(predictions=preds)

            # WEAR_GREEN, R_RATIO는 ppg ingest가 저장한 값 그대로 반환
            items.append(_serialize_with(r, preds))

        resp = Response({"ok": True, "items": items, "total": qs.count()})
        resp["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        resp["Pragma"]        = "no-cache"
        resp["Expires"]       = "0"
        return resp


@method_decorator(csrf_exempt, name="dispatch")
class BaselineView(APIView):
    """
    POST: '측정 시작' 버튼 클릭 시 호출 — 베이스라인 세션 시작.
    GET:  progress bar 갱신용 상태 조회.
    """
    authentication_classes = []

    def post(self, request):
        device_id = request.data.get("device_id") or "_default_"
        model_n_runtime.start_baseline_session(device_id)
        return Response({"ok": True, "device_id": device_id})

    def get(self, request):
        device_id = request.GET.get("device_id") or "_default_"
        st        = model_n_runtime.get_baseline_status(device_id)
        return Response({"ok": True, "device_id": device_id, **st})


class EventStatusView(APIView):
    """
    기존 ppg 앱의 EventStatusView와 동일합니다.
    dashboard_device.html의 IMU 상태/지도 표시에 사용합니다.
    """
    authentication_classes = []

    def get(self, request):
        device_id = request.GET.get("device_id")
        if not device_id:
            return Response({"ok": False, "error": "device_id required"}, status=400)

        protectee = Protectee.objects.filter(device_id=device_id).first()
        if not protectee:
            return Response({"ok": False, "error": "protectee not found"}, status=404)

        since = timezone.now() - timedelta(minutes=3)
        ev = (
            Event.objects
            .filter(protectee=protectee, timestamp__gte=since)
            .order_by("-timestamp")
            .first()
        )

        if not ev:
            return Response({
                "ok":               True,
                "device_id":        device_id,
                "protectee_name":   protectee.name,
                "imu_display":      "안정",
                "imu_danger_level": None,
                "latitude":         None,
                "longitude":        None,
                "timestamp":        None,
            })

        return Response({
            "ok":               True,
            "device_id":        device_id,
            "protectee_name":   protectee.name,
            "imu_display":      str(ev.imu_danger_level) if ev.imu_danger_level is not None else "안정",
            "imu_danger_level": ev.imu_danger_level,
            "latitude":         ev.latitude,
            "longitude":        ev.longitude,
            "timestamp":        ev.timestamp.isoformat(),
            "event_type":       ev.event_type,
        })


# ── 내부 헬퍼 ─────────────────────────────────────────────────────────────────

def _serialize(r) -> dict:
    """ORM 인스턴스 → dict. predictions는 DB 저장값 그대로입니다."""
    return {
        "id":          r.pk,
        "device_id":   r.device_id,
        "timestamp":   r.timestamp.isoformat() if r.timestamp else None,
        "ppg_green":   r.ppg_green,
        "ppg_ir":      r.ppg_ir,
        "predictions": r.predictions,
    }

def _serialize_with(r, preds: dict) -> dict:
    """
    ORM 인스턴스 + 방금 업데이트한 preds를 합쳐 반환합니다.
    DB에서 다시 읽지 않고 메모리의 preds를 바로 씁니다.
    """
    return {
        "id":          r.pk,
        "device_id":   r.device_id,
        "timestamp":   r.timestamp.isoformat() if r.timestamp else None,
        "ppg_green":   r.ppg_green,
        "ppg_ir":      r.ppg_ir,
        "predictions": preds,
    }