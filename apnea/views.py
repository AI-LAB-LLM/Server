import json
import logging
from datetime import datetime, timezone
from django.conf import settings
from django.http import JsonResponse
from django.shortcuts import render
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.csrf import csrf_exempt

from .apnea_engine import ApneaEngine, BASELINE_PACKETS
from .models import ApneaChunk, ApneaSession

logger = logging.getLogger(__name__)

def _norm(raw: str) -> str:
    # 콜론, 하이픈, 언더스코어 제거 후 소문자
    cleaned = raw.strip().lower().replace(":", "").replace("-", "").replace("_", "")
    # sml300 같은 prefix 제거 → 뒤 16자리만
    if len(cleaned) > 16:
        cleaned = cleaned[-16:]
    return cleaned


def _get_or_create_session(device_id: str) -> ApneaSession:
    session = (ApneaSession.objects
               .filter(device_id=device_id)
               .order_by('-started_at')
               .first())
    if session is None:
        session = ApneaSession.objects.create(
            device_id  = device_id,
            started_at = datetime.now(timezone.utc),
        )
    return session


@method_decorator(csrf_exempt, name='dispatch')
class IngestView(View):
    def post(self, request):
        try:
            body = json.loads(request.body)
        except Exception:
            return JsonResponse({"ok": False, "error": "invalid json"}, status=400)

        raw_id = body.get("device_id", "")
        if not raw_id:
            return JsonResponse({"ok": False, "error": "device_id required"}, status=400)

        device_id = _norm(raw_id)
        ppg_green = body.get("ppg_green", [])
        ppg_ir    = body.get("ppg_ir", [])    # ← 추가
        ppg_red   = body.get("ppg_red", [])   # ← 추가
        ts_str    = body.get("timestamp")

        if not isinstance(ppg_green, list) or len(ppg_green) == 0:
            return JsonResponse({"ok": False, "error": "ppg_green required"}, status=400)

        try:
            from dateutil import parser as dtp
            timestamp = dtp.parse(ts_str).astimezone(timezone.utc)
        except Exception:
            timestamp = datetime.now(timezone.utc)

        session = _get_or_create_session(device_id)
        engine  = ApneaEngine.get_instance()
        result  = engine.process_chunk(
            device_id, ppg_green,
            ppg_ir=ppg_ir, ppg_red=ppg_red,
            session_db=session
        )

        wear = result.get("wear", {})

        chunk = ApneaChunk.objects.create(
            session        = session,
            device_id      = device_id,
            timestamp      = timestamp,
            chunk_index    = result["packet_index"],
            ppg_green      = ppg_green,
            ppg_ir         = ppg_ir,
            ppg_red        = ppg_red,
            wear_valid     = wear.get("valid"),
            wear_label     = wear.get("label"),
            wear_prob      = wear.get("prob"),
            r_ratio_series = result.get("r_ratio_series"),
            is_baseline    = (result["phase"] == "baseline"),
            beat_results   = result.get("beat_results") or None,
            p_apnea        = result.get("p_apnea"),
            p_apnea_smooth = result.get("p_apnea_smooth"),
            pred_label     = result.get("pred_label"),
            pred_status    = result.get("pred_status"),
        )

        return JsonResponse({
            "ok":                True,
            "id":                chunk.pk,
            "phase":             result["phase"],
            "baseline_progress": result["baseline_progress"],
            "baseline_ready":    result["baseline_ready"],
            "wear":              wear,
            "p_apnea":           result.get("p_apnea"),
            "p_apnea_smooth":    result.get("p_apnea_smooth"),
            "pred_label":        result.get("pred_label"),
            "pred_status":       result.get("pred_status"),
        })


@method_decorator(csrf_exempt, name='dispatch')
class BaselineStartView(View):
    def post(self, request):
        try:
            body = json.loads(request.body)
        except Exception:
            return JsonResponse({"ok": False, "error": "invalid json"}, status=400)

        device_id = _norm(body.get("device_id", "_default_"))
        try:
            from dateutil import parser as dtp
            started_at = dtp.parse(body.get("started_at")).astimezone(timezone.utc)
        except Exception:
            started_at = datetime.now(timezone.utc)

        session = ApneaSession.objects.create(
            device_id      = device_id,
            started_at     = started_at,
            baseline_ready = False,
        )
        ApneaEngine.get_instance().start_session(device_id)

        return JsonResponse({
            "ok":         True,
            "session_id": session.pk,
            "device_id":  device_id,
            "started_at": started_at.isoformat(),
        })


class RecordsView(View):
    def get(self, request):
        device_id = request.GET.get("device_id")
        try:
            limit = int(request.GET.get("limit", 120))
        except ValueError:
            limit = 120

        qs = ApneaChunk.objects.order_by("-timestamp")
        if device_id:
            qs = qs.filter(device_id=_norm(device_id))
        qs = list(qs[:limit])
        qs.reverse()

        items = []
        for c in qs:
            predictions = {
                "WEAR_GREEN": {
                    "valid": c.wear_valid,
                    "label": c.wear_label,
                    "prob":  c.wear_prob,
                },
                "R_RATIO_SERIES": {
                    "values": c.r_ratio_series or [],
                },
            }
            if not c.is_baseline and c.p_apnea_smooth is not None:
                predictions["APNEA_RESULT"] = {
                    "prob":   c.p_apnea_smooth,
                    "label":  c.pred_label,
                    "valid":  c.pred_status == "ok",
                    "status": c.pred_status,
                }

            items.append({
                "id":           c.pk,
                "device_id":    c.device_id,
                "timestamp":    c.timestamp.isoformat(),
                "chunk_index":  c.chunk_index,
                "is_baseline":  c.is_baseline,
                "beat_results": c.beat_results or [],
                "predictions":  predictions,
            })

        return JsonResponse({"ok": True, "items": items, "total": len(items)})


class ModelStatusView(View):
    def get(self, request):
        engine = ApneaEngine.get_instance()
        return JsonResponse({
            "model_ready":  engine.model_ready,
            "model_config": engine.model_config,
        })


class DashboardView(View):
    def get(self, request):
        return render(request, "apnea/dashboard.html")
 

class DeviceDashboardView(View):
    def get(self, request, device_id):
        from django.db import connection

        # protectee 이름 조회
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT name FROM monitoring_protectee WHERE device_id = %s LIMIT 1",
                    [device_id]
                )
                row = cursor.fetchone()
                protectee_name = row[0] if row else device_id
        except Exception:
            protectee_name = device_id

        # 초기 데이터
        qs = ApneaChunk.objects.filter(
            device_id=_norm(device_id)
        ).order_by("-timestamp")[:120]
        chunks = list(qs)
        chunks.reverse()

        items = []
        for c in chunks:
            predictions = {
                "WEAR_GREEN": {
                    "valid": c.wear_valid,
                    "label": c.wear_label,
                    "prob":  c.wear_prob,
                },
                "R_RATIO_SERIES": {
                    "values": c.r_ratio_series or [],
                },
            }
            if not c.is_baseline and c.p_apnea_smooth is not None:
                predictions["APNEA_RESULT"] = {
                    "prob":   c.p_apnea_smooth,
                    "label":  c.pred_label,
                    "valid":  c.pred_status == "ok",
                    "status": c.pred_status,
                }
            items.append({
                "id":           c.pk,
                "device_id":    c.device_id,
                "timestamp":    c.timestamp.isoformat(),
                "chunk_index":  c.chunk_index,
                "is_baseline":  c.is_baseline,
                "beat_results": c.beat_results or [],
                "predictions":  predictions,
            })

        kakao_key = getattr(settings, "KAKAO_JS_KEY", "")

        return render(request, "apnea/dashboard_device.html", {
            "device_id":      device_id,
            "protectee_name": protectee_name,
            "items":          items,
            "KAKAO_JS_KEY":   kakao_key,
        })


class EventStatusView(View):
    """GET /apnea/api/event_status/?device_id=..."""
    def get(self, request):
        device_id = request.GET.get("device_id")
        if not device_id:
            return JsonResponse({"ok": False, "error": "device_id required"}, status=400)

        from django.utils import timezone
        from datetime import timedelta
        from django.db import connection

        since = timezone.now() - timedelta(minutes=3)

        # protectee 조회
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT id, name FROM monitoring_protectee WHERE device_id = %s LIMIT 1",
                    [device_id]
                )
                row = cursor.fetchone()
        except Exception as e:
            logger.warning(f"[EventStatusView] protectee query error: {e}")
            row = None

        if not row:
            return JsonResponse({"ok": False, "error": "protectee not found"}, status=404)

        protectee_id, protectee_name = row

        # 최근 3분 IMU 이벤트
        try:
            with connection.cursor() as cursor:
                cursor.execute("""
                    SELECT imu_danger_level, timestamp, latitude, longitude
                    FROM monitoring_event
                    WHERE protectee_id = %s
                      AND timestamp >= %s
                    ORDER BY timestamp DESC
                    LIMIT 1
                """, [protectee_id, since])
                ev = cursor.fetchone()
        except Exception as e:
            logger.warning(f"[EventStatusView] event query error: {e}")
            ev = None

        # 최신 위치 (geo_processed_data)
        try:
            with connection.cursor() as cursor:
                cursor.execute("""
                    SELECT latitude, longitude
                    FROM geo_processed_data
                    WHERE protectee_id = %s
                      AND pos_success = 1
                    ORDER BY id DESC
                    LIMIT 1
                """, [protectee_id])
                geo = cursor.fetchone()
        except Exception as e:
            logger.warning(f"[EventStatusView] geo query error: {e}")
            geo = None

        # 착용 여부 (최신 chunk)
        latest_chunk = (ApneaChunk.objects
                        .filter(device_id=_norm(device_id))
                        .order_by("-timestamp")
                        .first())
        wear = {
            "valid": latest_chunk.wear_valid if latest_chunk else None,
            "label": latest_chunk.wear_label if latest_chunk else None,
            "prob":  latest_chunk.wear_prob  if latest_chunk else None,
        }

        lat = geo[0] if geo else (ev[2] if ev else None)
        lon = geo[1] if geo else (ev[3] if ev else None)

        return JsonResponse({
            "ok":               True,
            "device_id":        device_id,
            "protectee_name":   protectee_name,
            "imu_display":      str(ev[0]) if ev and ev[0] is not None else "안정",
            "imu_danger_level": ev[0] if ev else None,
            "latitude":         float(lat) if lat is not None else None,
            "longitude":        float(lon) if lon is not None else None,
            "timestamp":        ev[1].isoformat() if ev and ev[1] else None,
            "wear":             wear,
        })