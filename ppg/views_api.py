from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from drf_spectacular.utils import extend_schema, OpenApiParameter

from .models import SensorData
from . import model_runtime
from .utils import serialize_sensor_row, fix_len_pad_trim, fix_pair_same_len
from .serializers import (
    RecordsResponseSerializer,
    IngestRequestSerializer,
    IngestResponseSerializer,
)
from .utils_spo2 import compute_R_series_1s
from django.views.decorators.csrf import csrf_exempt
from django.utils.decorators import method_decorator

import logging
import json
from zoneinfo import ZoneInfo
from django.utils.dateparse import parse_datetime
from django.utils import timezone
from datetime import timedelta
from monitoring.models import Protectee, Event


access_log = logging.getLogger("django.request")
KST = ZoneInfo("Asia/Seoul")

@method_decorator(csrf_exempt, name='dispatch')
class RecordsView(APIView):
    authentication_classes = []
    MAX_ITEMS = 120

    @extend_schema(
        parameters=[
            OpenApiParameter(name="limit", type=int, required=False, description="최근 N개 (기본 20, 최대 200)"),
            OpenApiParameter(name="minutes", type=int, required=False, description="최근 X분(선택)"),
        ],
        tags=["dashboard"],
        summary="대시보드용 최근 레코드 조회",
        responses=RecordsResponseSerializer,
        request=None,
    )
    def get(self, request):
        # limit 안전 처리
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
                from django.utils import timezone
                from datetime import timedelta
                since = timezone.now() - timedelta(minutes=int(minutes))
                qs = qs.filter(timestamp__gte=since)
            except ValueError:
                pass

        #rows = list(qs.order_by("-timestamp")[:limit])
        #items = [serialize_sensor_row(r) for r in rows][::-1]
        rows = list(qs.order_by("-id")[:limit])
        items = [serialize_sensor_row(r) for r in rows][::-1]

        total = qs.count()

        from rest_framework.response import Response
        resp = Response({"ok": True, "items": items, "total": total})
        resp['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
        resp['Pragma'] = 'no-cache'
        resp['Expires'] = '0'

        return resp

@method_decorator(csrf_exempt, name='dispatch')
class IngestView(APIView):
    authentication_classes = []

    @extend_schema(
        tags=["collect"],
        summary="워치가 1 청크(12s) 전송 — R은 1초마다 계산",
        request=IngestRequestSerializer,
        responses=IngestResponseSerializer,
    )
    def post(self, request):
        ser = IngestRequestSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        p = ser.validated_data

        print("[RAW DATA]", p.get("device_id"))
        print("[PPG GREEN]", p.get("ppg_green")[:10])
        print("[PPG IR]", p.get("ppg_ir")[:10])
        print("[PPG RED]", p.get("ppg_red")[:10])

        # timestamp: ISO8601 → aware(KST)
        try:
            dt = parse_datetime(str(p["timestamp"]))
            if not dt:
                raise ValueError("invalid ISO8601 format")
            if timezone.is_naive(dt):
                dt = timezone.make_aware(dt, timezone=timezone.utc)
            ts_kst = dt.astimezone(KST)
        except Exception:
            return Response(
                {"ok": False, "error": "timestamp must be valid ISO8601 string (e.g. 2025-10-13T12:34:56.000Z)"},
                status=400,
            )

        device_id = str(p["device_id"])[:64]
        ppg_green = p["ppg_green"]
        ppg_ir    = p["ppg_ir"]
        ppg_red   = p["ppg_red"]

        access_log.info(
            f"[ingest] device_id={device_id} lens(green,ir,red)={[len(ppg_green), len(ppg_ir), len(ppg_red)]}"
        )

        # 표준화 파라미터
        DURATION_SEC = 12.0
        SR_GREEN  = 25.0
        SR_IRRED  = 100.0
        LEN_GREEN = int(DURATION_SEC * SR_GREEN)   # 300
        LEN_IRRED = int(DURATION_SEC * SR_IRRED)   # 1200

        # 길이 보정 유틸
        def _safe_fix_green_len(x, target_len=300):
            """
            green은 299/300/301만 오므로, 값 왜곡 없는 pad/trim만 수행.
            (보간, 재샘플, 스케일 변화 금지)
            """
            try:
                n = len(x)
                if n == target_len:
                    return x
                if n == target_len - 1:  # 299 → 300 : 마지막 값 복제
                    return list(x) + [x[-1]]
                if n == target_len + 1:  # 301 → 300 : 앞 300개만 사용
                    return list(x)[:target_len]
                return fix_len_pad_trim(x, target_len, max_delta=2)
            except Exception:
                return x

        def _safe_fix_irred_len(x, target_len=1200):
            """
            IR/RED 신호도 보간 없이 pad/trim만 수행.
            (값 재계산이나 스케일 변화 금지)
            """
            try:
                n = len(x)
                if n == target_len:
                    return x
                if n < target_len:
                    pad_n = target_len - n
                    return list(x) + [x[-1]] * pad_n  # 마지막 값 복제
                if n > target_len:
                    return list(x)[:target_len]
                return x
            except Exception:
                return x

        def _safe_fix_pair(a, b, target_len):
            """
            IR/RED 쌍의 길이를 동일하게 맞춤.
            (보간 금지, pad/trim만 수행)
            """
            try:
                a_fixed = _safe_fix_irred_len(a, target_len)
                b_fixed = _safe_fix_irred_len(b, target_len)
                return a_fixed, b_fixed
            except Exception:
                return a, b

        # 길이 표준화
        g_std = _safe_fix_green_len(ppg_green, LEN_GREEN)
        ir_std = _safe_fix_irred_len(ppg_ir, LEN_IRRED)
        ir_std_for_R, red_std_for_R = _safe_fix_pair(ppg_ir, ppg_red, LEN_IRRED)

        access_log.info(
            f"[normalized] device_id={device_id} lens(green,ir,red)={[len(g_std), len(ir_std_for_R), len(red_std_for_R)]}"
        )

        # 추론 (표준화된 green/ir 사용)
        try:
            access_log.info("[VR_CALL] will_call_predict_all ir_is_none=%s ir_len=%s",
                        ppg_ir is None, (len(ppg_ir) if ppg_ir is not None else None))
            preds = model_runtime.predict_all(g_std, ir_std, uuid=device_id) or {}

            wear = preds.get("WEAR_GREEN", {}) or {}
            ir = preds.get("IR_HOLDING", {}) or {}

            access_log.info(
                "[VR_RET] wear_valid=%s ir_valid=%s ir_err=%s",
                wear.get("valid"), ir.get("valid"), ir.get("error")
            )
        except Exception as e:
            preds = {"_error": type(e).__name__}

        # R 시리즈 (표준화된 IR/RED 사용
        import math
        def _to_float_or_none(x):
            try:
                v = float(x)
                if math.isnan(v) or math.isinf(v):
                    return None
                return v
            except Exception:
                return None

        try:
            r_series_raw = compute_R_series_1s(
                ir_std_for_R, red_std_for_R,
                sr=SR_IRRED, window_sec=1.0, dc_win_sec=1.2
            )
            r_series = [_to_float_or_none(v) for v in list(r_series_raw or [])]

            exp_len = int(DURATION_SEC / 1.0)  # 12
            if len(r_series) != exp_len:
                access_log.warning(
                    "[R] unexpected series len: got=%d expect=%d (ir=%d, red=%d, sr=%.1f)",
                    len(r_series), exp_len, len(ir_std_for_R), len(red_std_for_R), SR_IRRED
                )

            last_val = next((v for v in reversed(r_series) if v is not None), None)
            preds["R_RATIO"] = {
                "value": last_val,
                "valid": any(v is not None for v in r_series),
            }
            preds["R_RATIO_SERIES"] = {"values": r_series, "window_sec": 1.0, "sr": SR_IRRED}

            access_log.info(
                f"[normalized] device_id={device_id} R_len={len(r_series)} last={last_val}"
            )
        except Exception as e:
            preds["R_RATIO"] = {"value": None, "valid": False, "error": type(e).__name__}
            preds["R_RATIO_SERIES"] = {"values": [], "error": type(e).__name__}
            access_log.exception("[R] compute failed")

        rec = SensorData.objects.create(
            device_id=device_id,
            timestamp=ts_kst,
            ppg_green=ppg_green,
            ppg_ir=ppg_ir,
            ppg_red=ppg_red,
            predictions=preds or None,
        )

        status_obj = model_runtime.get_status()
        access_log.info("[ingest] model_status: %s", json.dumps(status_obj, ensure_ascii=False))

        return Response(
            {"ok": True, "id": rec.id, "predictions": preds, "timestamp": ts_kst.isoformat(), "_models": status_obj},
            status=status.HTTP_200_OK,
        )

class BaselineSessionView(APIView):
    authentication_classes = []
    def post(self, request):
        device_id = request.data.get("device_id") or "_default_"
        from . import ir_runtime
        ir_runtime.start_baseline_session(device_id)
        return Response({"ok": True})

    def get(self, request):
        device_id = request.GET.get("device_id") or "_default_"
        from . import ir_runtime
        st = ir_runtime._get_state(device_id)
        stats = st.stats() if st else {"mu": {}, "sd": {}}
        active = bool(ir_runtime._SESSION_ACTIVE.get(device_id))
        return Response({"active": active, "stats": stats, "device_id": device_id})


class EventStatusView(APIView):
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
                "ok": True,
                "device_id": device_id,
                "protectee_name": protectee.name,
                "imu_display": "안정",
                "imu_danger_level": None,
                "latitude": None,
                "longitude": None,
                "timestamp": None,
            })

        imu_level = ev.imu_danger_level
        imu_display = str(imu_level) if imu_level is not None else "안정"

        return Response({
            "ok": True,
            "device_id": device_id,
            "protectee_name": protectee.name,
            "imu_display": imu_display,
            "imu_danger_level": imu_level,
            "latitude": ev.latitude,
            "longitude": ev.longitude,
            "timestamp": ev.timestamp.isoformat(),
            "event_type": ev.event_type,
        })
