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
from .models import ApneaChunk, ApneaSession, ApneaDeviceConfig


logger = logging.getLogger(__name__)

def _norm(raw: str) -> str:
    # 콜론, 하이픈, 언더스코어 제거 후 소문자
    cleaned = raw.strip().lower().replace(":", "").replace("-", "").replace("_", "")
    # sml300 같은 prefix 제거 → 뒤 16자리만
    if len(cleaned) > 16:
        cleaned = cleaned[-16:]
    return cleaned

# 그 장치의 가장 최근 세션을 가져오거나, 없으면 새로 만듦 
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
        received_at = datetime.now(timezone.utc)  # ← 추가

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

        
        engine  = ApneaEngine.get_instance()
        session_pk = engine._session_pk.get(device_id)
        if session_pk:
            try:
                session = ApneaSession.objects.get(pk=session_pk)
            except ApneaSession.DoesNotExist:
                session = None          # ← 옛 세션 끌어오지 않음
        else:
            session = None             # ← _get_or_create_session 호출 제거

        result  = engine.process_chunk(
            device_id, ppg_green,
            ppg_ir=ppg_ir, ppg_red=ppg_red,
            session_db=session,
            packet_timestamp=timestamp, 
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
            is_baseline=result.get(
                "is_baseline",
                False,
            ),            
            beat_results   = result.get("beat_results") or None,
            p_apnea        = result.get("p_apnea"),
            p_apnea_smooth = result.get("p_apnea_smooth"),
            pred_label     = result.get("pred_label"),
            pred_status    = result.get("pred_status"),
            received_at    = received_at,
        )

        # PPG 데이터 저장 후 SOS 판정
        from .sos_services import check_and_create_sos

        try:
            incident = check_and_create_sos(device_id)

            if incident is not None:
                logger.info(
                    "[SOS] device=%s incident=%s rule=%s",
                    device_id,
                    incident.id,
                    incident.rule,
                )

        except Exception:
            logger.exception(
                "[SOS] 판정 실패 device=%s",
                device_id,
            )

        # 기존 전송 지연 계산 코드
        try:
            watch_end_time = timestamp + __import__('datetime').timedelta(seconds=12)
            network_delay = (received_at - watch_end_time).total_seconds()
            print(f"[딜레이] received_at={received_at.isoformat()}, watch_end={watch_end_time.isoformat()}, 전송지연={network_delay:.2f}s", flush=True)
        except Exception:
                    pass

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
        ApneaEngine.get_instance().start_session(device_id, session_pk=session.pk)

        return JsonResponse({
            "ok":         True,
            "session_id": session.pk,
            "device_id":  device_id,
            "started_at": started_at.isoformat(),
        })

class BaselineListView(View):
    def get(self, request):
        device_id = _norm(
            request.GET.get("device_id", "")
        )

        if not device_id:
            return JsonResponse(
                {
                    "ok": False,
                    "error": "device_id is required",
                },
                status=400,
            )

        # 기존 ApneaSession에서 baseline 완료된 것만 가져옴
        sessions = (
            ApneaSession.objects
            .filter(
                device_id=device_id,
                baseline_ready=True,
                baseline_stats__isnull=False,
            )
            .order_by("-started_at")
        )

        config = (
            ApneaDeviceConfig.objects
            .filter(device_id=device_id)
            .first()
        )

        selected_session_id = (
            config.selected_baseline_session_id
            if config
            else None
        )

        items = []

        for session in sessions:
            stats = session.baseline_stats or {}

            items.append({
                "session_id": session.pk,
                "measured_at": session.started_at.isoformat(),
                "num_beats": stats.get("num_beats"),
                "selected": (
                    session.pk == selected_session_id
                ),
            })

        return JsonResponse({
            "ok": True,
            "device_id": device_id,
            "selected_session_id": selected_session_id,
            "items": items,
        })

@method_decorator(csrf_exempt, name="dispatch")
class BaselineSelectView(View):
    def post(self, request):
        try:
            body = json.loads(request.body)
        except Exception:
            return JsonResponse(
                {
                    "ok": False,
                    "error": "invalid json",
                },
                status=400,
            )

        device_id = _norm(
            body.get("device_id", "")
        )

        session_id = body.get("session_id")

        if not device_id or not session_id:
            return JsonResponse(
                {
                    "ok": False,
                    "error": (
                        "device_id and session_id "
                        "are required"
                    ),
                },
                status=400,
            )

        # 반드시 같은 워치의 baseline만 선택 가능
        try:
            session = ApneaSession.objects.get(
                pk=session_id,
                device_id=device_id,
                baseline_ready=True,
                baseline_stats__isnull=False,
            )

        except ApneaSession.DoesNotExist:
            return JsonResponse(
                {
                    "ok": False,
                    "error": (
                        "baseline session not found "
                        "for this device"
                    ),
                },
                status=404,
            )

        engine = ApneaEngine.get_instance()

        # 먼저 실제 엔진에 적용
        applied = engine.select_baseline_session(
            device_id=device_id,
            session=session,
        )

        if not applied:
            return JsonResponse(
                {
                    "ok": False,
                    "error": "failed to apply baseline",
                },
                status=500,
            )

        # 적용 성공했을 때만 DB 선택값 변경
        config, _ = (
            ApneaDeviceConfig.objects.get_or_create(
                device_id=device_id
            )
        )

        config.selected_baseline_session = session

        config.save(
            update_fields=[
                "selected_baseline_session",
                "updated_at",
            ]
        )

        return JsonResponse({
            "ok": True,
            "device_id": device_id,
            "session_id": session.pk,
            "measured_at": session.started_at.isoformat(),
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
                "received_at":  c.received_at.isoformat() if c.received_at else None,
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
                "received_at":  c.received_at.isoformat() if c.received_at else None,
            })


        return render(request, "apnea/dashboard_device.html", {
            "device_id":      device_id,
            "protectee_name": protectee_name,
            "items":          items,
        })


class EventStatusView(View):
    """
    GET /apnea/api/event_status/?device_id=...

    최신 IMU 위험 레벨과 최근 3분의 레벨 이력 반환.
    """

    def get(self, request):
        from datetime import timedelta
        from django.utils import timezone
        from monitoring.models import Event

        device_id = request.GET.get("device_id")

        if not device_id:
            return JsonResponse({
                "ok": False,
                "error": "device_id required",
            }, status=400)

        now = timezone.now()
        since = now - timedelta(minutes=3)

        qs = Event.objects.filter(
            protectee__device_id=device_id,
            event_type=Event.EventType.IMU_ALERT,
            timestamp__gte=since,
            timestamp__lte=now,
        )

        latest = (
            qs.order_by("-timestamp", "-id")
            .first()
        )

        if latest is None:
            return JsonResponse({
                "ok": True,
                "imu_display": "데이터 없음",
                "imu_danger_level": None,
                "timestamp": None,
                "history": [],
            })

        rows = (
            qs.order_by("timestamp", "id")
            .values(
                "id",
                "timestamp",
                "imu_danger_level",
            )
        )

        history = []

        for row in rows:
            history.append({
                "id": row["id"],
                "timestamp": row["timestamp"].isoformat(),
                "level": row["imu_danger_level"],
                "offset_sec": (
                    now - row["timestamp"]
                ).total_seconds(),
            })

        level = latest.imu_danger_level

        if level is None:
            display = "데이터 없음"
        elif level >= 4:
            display = "위험"
        elif level >= 2:
            display = "주의"
        else:
            display = "안정"

        return JsonResponse({
            "ok": True,
            "imu_display": display,
            "imu_danger_level": level,
            "timestamp": latest.timestamp.isoformat(),
            "result_id": latest.id,
            "history": history,
        })


class SosStatusView(View):
    """
    GET /apnea/api/sos/status/?device_id=...
    """

    def get(self, request):
        from django.utils import timezone

        from .models import SosIncident
        from .sos_services import check_and_create_sos

        device_id = request.GET.get("device_id")

        if not device_id:
            return JsonResponse({
                "ok": False,
                "error": "device_id required",
            }, status=400)

        # 현재 저장된 센서 결과로 SOS 판정
        check_and_create_sos(device_id)

        incident = (
            SosIncident.objects
            .filter(
                device_id=device_id,
                closed_at__isnull=True,
            )
            .order_by("-occurred_at")
            .first()
        )

        if incident is None:
            return JsonResponse({
                "ok": True,
                "active": False,
                "sos": None,
            })

        return JsonResponse({
            "ok": True,
            "active": True,
            "sos": {
                "incident_id": incident.id,
                "rule": incident.rule,
                "occurred_at": incident.occurred_at.isoformat(),
                "elapsed_sec": max(
                    0,
                    (
                        timezone.now() - incident.occurred_at
                    ).total_seconds(),
                ),
                **incident.detail,
            },
        })


@method_decorator(csrf_exempt, name="dispatch")
class SosCloseView(View):
    """
    POST /apnea/api/sos/close/

    body:
    {
        "device_id": "...",
        "incident_id": 1
    }
    """

    def post(self, request):
        import json

        from .sos_services import close_sos

        try:
            body = json.loads(request.body or b"{}")
        except (ValueError, TypeError):
            return JsonResponse({
                "ok": False,
                "error": "invalid json",
            }, status=400)

        device_id = body.get("device_id")
        incident_id = body.get("incident_id")

        if (
            not device_id
            or type(incident_id) is not int
        ):
            return JsonResponse({
                "ok": False,
                "error": "device_id, incident_id required",
            }, status=400)

        incident = close_sos(
            device_id=device_id,
            incident_id=incident_id,
        )

        if incident is None:
            return JsonResponse({
                "ok": False,
                "error": "SOS incident not found",
            }, status=404)

        return JsonResponse({
            "ok": True,
            "closed": True,
            "incident_id": incident.id,
            "closed_at": incident.closed_at.isoformat(),
        })

    