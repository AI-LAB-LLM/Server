# apnea/sos_services.py

import logging
from datetime import timedelta

from django.db import transaction
from django.db.models import Max
from django.utils import timezone

from monitoring.models import Event, Protectee
from .models import ApneaChunk, SosIncident
logger = logging.getLogger(__name__)

SOS_WINDOW_SEC = 30

# 참고 서버와 동일한 SOS 판정 기준
PPG_TRUE_MIN_WITH_IMU = 1
IMU_TRUE_CONSECUTIVE_MIN = 3
PPG_TRUE_MIN_ALONE = 3

# 현재 서버의 위협 판정 기준
IMU_THREAT_MIN_LEVEL = 4
PPG_THREAT_VALUE = 1


def _max_consecutive_true(flags):
    """시간순 Boolean 목록에서 최대 연속 True 횟수."""
    best = 0
    run = 0

    for flag in flags:
        if flag:
            run += 1
            best = max(best, run)
        else:
            run = 0

    return best


def evaluate_sos(device_id, t, after=None, after_event_id=None):
    """
    최근 30초 SOS 판정.

    PPG: ApneaChunk.pred_label
    IMU: monitoring.Event.imu_danger_level

    기존 판정 기준 유지:
      1) PPG 1회 이상 + IMU 위험 3회 연속
      2) PPG 3회 이상
    """
    window_start = t - timedelta(seconds=SOS_WINDOW_SEC)

    if after is not None:
        window_start = max(window_start, after)

    # IMU는 기존 Event 테이블에서 조회
    imu_events = Event.objects.filter(
        protectee__device_id=device_id,
        timestamp__gt=window_start,
        timestamp__lte=t,
        event_type=Event.EventType.IMU_ALERT,
    )

    if after_event_id is not None:
        imu_events = imu_events.filter(
            id__gt=after_event_id
        )

    imu_levels = list(
        imu_events
        .order_by("timestamp", "id")
        .values_list("imu_danger_level", flat=True)
    )

    imu_flags = [
        level is not None and level >= IMU_THREAT_MIN_LEVEL
        for level in imu_levels
    ]

    # PPG는 ApneaChunk에서 조회
    ppg_chunks = ApneaChunk.objects.filter(
        device_id=device_id,
        timestamp__gt=window_start,
        timestamp__lte=t,
        is_baseline=False,
        pred_status="ok",
    )

    # SOS 종료 이전 PPG 데이터 재사용 방지
    if after is not None:
        ppg_chunks = ppg_chunks.filter(
            timestamp__gt=after
        )

    ppg_labels = list(
        ppg_chunks
        .order_by("timestamp", "id")
        .values_list("pred_label", flat=True)
    )

    ppg_flags = [
        label == PPG_THREAT_VALUE
        for label in ppg_labels
    ]

    ppg_true = sum(ppg_flags)
    imu_streak = _max_consecutive_true(imu_flags)

    detail = {
        "ppg_true": ppg_true,
        "ppg_count": len(ppg_flags),
        "imu_streak": imu_streak,
        "imu_count": len(imu_flags),
        "imu_max_level": max(
            (x for x in imu_levels if x is not None),
            default=None,
        ),
        "ppg_max_level": max(
            (x for x in ppg_labels if x is not None),
            default=None,
        ),
        "rule": None,
    }

    if (
        ppg_true >= PPG_TRUE_MIN_WITH_IMU
        and imu_streak >= IMU_TRUE_CONSECUTIVE_MIN
    ):
        detail["rule"] = "physical+psychological"
        return True, detail

    if ppg_true >= PPG_TRUE_MIN_ALONE:
        detail["rule"] = "psychological"
        return True, detail

    return False, detail


def _latest_closed_incident(device_id):
    return (
        SosIncident.objects
        .filter(
            device_id=device_id,
            closed_at__isnull=False,
        )
        .order_by("-closed_at", "-id")
        .first()
    )


def _latest_event_timestamp(device_id):
    imu_latest = (
        Event.objects.filter(
            protectee__device_id=device_id,
            event_type=Event.EventType.IMU_ALERT,
        )
        .aggregate(latest=Max("timestamp"))
    )["latest"]

    ppg_latest = (
        ApneaChunk.objects.filter(
            device_id=device_id,
            is_baseline=False,
            pred_status="ok",
        )
        .aggregate(latest=Max("timestamp"))
    )["latest"]

    timestamps = [
        ts for ts in (imu_latest, ppg_latest)
        if ts is not None
    ]

    return max(timestamps) if timestamps else None

def check_and_create_sos(device_id):
    """
    최신 PPG/IMU 데이터를 기준으로 SOS 판정.

    - 최근 30초 데이터 사용
    - 미종결 SOS 중복 생성 방지
    - 종료된 사건 이전 데이터 재사용 방지
    - 조건 만족 시 SosIncident 생성
    """
    if not device_id:
        return None

    try:
        with transaction.atomic():

            # 동일 기기의 동시 요청으로 인한 중복 생성 방지
            Protectee.objects.select_for_update().get(
                device_id=device_id
            )

            # 이미 진행 중인 SOS가 있으면 반환
            active = (
                SosIncident.objects
                .filter(
                    device_id=device_id,
                    closed_at__isnull=True,
                )
                .order_by("-detected_at", "-id")
                .first()
            )

            if active is not None:
                return active

            # 최신 PPG 또는 IMU 측정 시각
            latest_ts = _latest_event_timestamp(device_id)

            if latest_ts is None:
                logger.info(
                    "[SOS] 데이터 없음 device=%s",
                    device_id,
                )
                return None

            # 마지막으로 종료된 SOS
            last_closed = _latest_closed_incident(device_id)

            # 현재 데이터가 너무 오래된 경우 생성하지 않음
            delay = (
                timezone.now() - latest_ts
            ).total_seconds()

            if delay > SOS_WINDOW_SEC:
                logger.info(
                    "[SOS] 오래된 데이터 device=%s delay=%.1fs",
                    device_id,
                    delay,
                )
                return None

            # SOS 판정
            detected, detail = evaluate_sos(
                device_id=device_id,
                t=latest_ts,
                after=(
                    last_closed.closed_at
                    if last_closed is not None
                    else None
                ),
                after_event_id=(
                    last_closed.closed_event_id
                    if last_closed is not None
                    else None
                ),
            )

            logger.info(
                "[SOS CHECK] device=%s "
                "ppg_true=%s/%s "
                "imu_streak=%s "
                "detected=%s",
                device_id,
                detail["ppg_true"],
                detail["ppg_count"],
                detail["imu_streak"],
                detected,
            )

            if not detected:
                return None

            # SOS 사건 저장
            incident = SosIncident.objects.create(
                device_id=device_id,
                detected_at=latest_ts,
                rule=detail["rule"],
                detail=detail,
            )

            logger.warning(
                "[SOS CREATED] device=%s "
                "incident=%s rule=%s",
                device_id,
                incident.id,
                incident.rule,
            )

            return incident

    except Protectee.DoesNotExist:
        logger.warning(
            "[SOS] 등록되지 않은 device_id=%s",
            device_id,
        )
        return None
    

def close_sos(device_id, incident_id):
    from monitoring.models import Event

    with transaction.atomic():
        incident = (
            SosIncident.objects
            .select_for_update()
            .filter(
                id=incident_id,
                device_id=device_id,
            )
            .first()
        )

        if incident is None:
            return None

        if incident.closed_at is None:
            latest_event = (
                Event.objects
                .filter(
                    protectee__device_id=device_id,
                    event_type__in=[
                        Event.EventType.IMU_ALERT,
                        Event.EventType.PPG_ALERT,
                    ],
                )
                .order_by("-id")
                .first()
            )

            incident.closed_event_id = (
                latest_event.id
                if latest_event is not None
                else None
            )

            incident.closed_at = timezone.now()

            incident.save(
                update_fields=[
                    "closed_at",
                    "closed_event_id",
                ]
            )

        return incident