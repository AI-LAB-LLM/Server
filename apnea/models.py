from django.db import models


class ApneaSession(models.Model):
    device_id      = models.CharField(max_length=64, db_index=True)
    started_at     = models.DateTimeField()
    baseline_ready = models.BooleanField(default=False)
    baseline_stats = models.JSONField(null=True, blank=True)
    model_config   = models.JSONField(null=True, blank=True)
    created_at     = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-started_at']

    def __str__(self):
        return f"Session({self.device_id} @ {self.started_at.isoformat()})"


class ApneaChunk(models.Model):
    session        = models.ForeignKey(
        ApneaSession, on_delete=models.CASCADE,
        related_name='chunks', null=True, blank=True
    )
    device_id      = models.CharField(max_length=64, db_index=True)
    received_at    = models.DateTimeField(null=True, blank=True)  
    timestamp      = models.DateTimeField(db_index=True)
    chunk_index    = models.IntegerField(default=0)

    ppg_green      = models.JSONField(default=list)
    ppg_ir         = models.JSONField(default=list) 
    ppg_red        = models.JSONField(default=list)   

    wear_valid     = models.BooleanField(null=True)
    wear_label     = models.IntegerField(null=True)
    wear_prob      = models.FloatField(null=True)
    r_ratio_series = models.JSONField(null=True, blank=True)
    is_baseline    = models.BooleanField(default=False)
    beat_results   = models.JSONField(null=True, blank=True)
    p_apnea        = models.FloatField(null=True)
    p_apnea_smooth = models.FloatField(null=True)
    pred_label     = models.IntegerField(null=True)
    pred_status    = models.CharField(max_length=32, null=True, blank=True)
    created_at     = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['timestamp']

    def __str__(self):
        return f"Chunk({self.device_id}#{self.chunk_index})"


class ApneaDeviceConfig(models.Model):
    device_id = models.CharField(
        max_length=64,
        unique=True,
        db_index=True,
    )

    # 현재 이 워치가 추론에 사용하는 baseline session
    selected_baseline_session = models.ForeignKey(
        ApneaSession,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )

    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return (
            f"DeviceConfig("
            f"{self.device_id}, "
            f"baseline={self.selected_baseline_session_id}"
            f")"
        )


class SosIncident(models.Model):
    RULE_CHOICES = [
        ("physical+psychological", "물리 및 심리적 이상"),
        ("psychological", "심리적 이상"),
    ]

    device_id = models.CharField(
        max_length=64,
        db_index=True,
    )

    # 실제 SOS를 유발한 센서 이벤트 시각
    detected_at = models.DateTimeField(db_index=True)

    # 대시보드에서 사건이 발생한 시각
    occurred_at = models.DateTimeField(auto_now_add=True)

    # 사건 종결 시각. NULL이면 미종결 상태
    closed_at = models.DateTimeField(
        null=True,
        blank=True,
    )

    # SOS 발생 원인
    rule = models.CharField(
        max_length=32,
        choices=RULE_CHOICES,
    )

    # SOS 발생 당시의 판단 근거
    detail = models.JSONField(default=dict)
    closed_event_id = models.BigIntegerField(
    null=True,
    blank=True,
    )

    class Meta:
        ordering = ["-occurred_at"]
        indexes = [
            models.Index(fields=["device_id", "closed_at"]),
        ]

    def __str__(self):
        return (
            f"SOS(device={self.device_id}, "
            f"rule={self.rule}, "
            f"closed={self.closed_at is not None})"
        )