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