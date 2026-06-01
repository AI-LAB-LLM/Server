import logging
from django.apps import AppConfig

logger = logging.getLogger(__name__)


class ApneaConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name               = "apnea"
    verbose_name       = "Apnea Detection"

    def ready(self):
        try:
            self._load_model()
            self._restore_baselines()
        except Exception as e:
            logger.error(f"[ApneaConfig] ready() failed: {e}")

    def _load_model(self):
        from pathlib import Path
        from django.conf import settings

        base_dir   = Path(getattr(settings, "BASE_DIR", Path(__file__).resolve().parents[1]))
        model_path = base_dir / "media" / "models" / "ppg" / "best_c_stream_change_model.pt"

        if not model_path.exists():
            logger.warning(f"[ApneaConfig] model not found: {model_path}")
            return

        from .apnea_engine import ApneaEngine
        ok = ApneaEngine.get_instance().load_model(str(model_path))
        if ok:
            logger.info("[ApneaConfig] model ready")
        else:
            logger.error("[ApneaConfig] model load failed")

    def _restore_baselines(self):
        try:
            import numpy as np
            import torch
            from .apnea_engine import (ApneaEngine, RealtimeApneaDetector,
                                       RealtimeBeatExtractor, FS)
            from .models import ApneaSession

            engine = ApneaEngine.get_instance()
            if not engine.model_ready:
                logger.warning("[restore] model not ready, skip")
                return

            # device_id별 최신 세션 (SQLite 호환)
            seen = set()
            sessions = ApneaSession.objects.filter(baseline_ready=True).order_by('-id')

            for session in sessions:
                if session.device_id in seen:
                    continue
                seen.add(session.device_id)

                try:
                    device_id = session.device_id
                    stats     = session.baseline_stats
                    cfg       = session.model_config or {}

                    if not stats:
                        continue

                    ref_mu      = np.array(stats['ref_mu'], dtype=np.float32)
                    ref_sd      = np.array(stats['ref_sd'], dtype=np.float32)
                    context_len = int(cfg.get('context_len', 20))
                    threshold   = float(cfg.get('threshold', 0.75))
                    device_str  = 'cuda' if torch.cuda.is_available() else 'cpu'

                    detector = RealtimeApneaDetector(
                        model                = engine._model,
                        context_len          = context_len,
                        ref_mu               = ref_mu,
                        ref_sd               = ref_sd,
                        threshold_from_train = threshold,
                        device               = device_str,
                    )
                    # ★ 복원 시에는 beat_window 미리 채우기 안 함
                    # (측정 직후에만 _finalize_baseline()에서 채움)

                    extractor = RealtimeBeatExtractor(
                        fs=FS, rolling_seconds=13.0, safe_margin_seconds=0.6
                    )

                    with engine._dev_lock:
                        engine._detectors[device_id]     = detector
                        engine._extractors[device_id]    = extractor
                        engine._baseline_done[device_id] = True
                        engine._baseline_buf[device_id]  = []
                        engine._packet_count[device_id]  = 999

                    logger.info(f"[restore] baseline restored: {device_id}")

                except Exception as e:
                    logger.warning(f"[restore] failed for {session.device_id}: {e}")

        except Exception as e:
            logger.warning(f"[restore] baseline restore failed: {e}")