import logging
from django.apps import AppConfig

logger = logging.getLogger(__name__)


class ApneaConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name               = "apnea"
    verbose_name       = "Apnea Detection"

    def ready(self):
        self._load_model()

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