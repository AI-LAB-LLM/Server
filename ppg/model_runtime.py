import logging, threading, importlib
from pathlib import Path
from typing import Dict, Any, Optional
from django.conf import settings

from . import ir_runtime
logger = logging.getLogger(__name__)

KEY_IR   = "IR_HOLDING"
KEY_WEAR = "WEAR_GREEN"

_IR_READY: bool = False
_LOCK = threading.Lock()

BASE_DIR = Path(getattr(settings, "BASE_DIR", Path(__file__).resolve().parents[1]))
IR_PATH = BASE_DIR / "media" / "models" / "ir_model.joblib"

def get_status() -> Dict[str, Any]:
    try:
        exists = IR_PATH.exists()
    except Exception:
        exists = None
    return {"ir": {"ready": bool(_IR_READY), "path": str(IR_PATH), "exists": exists}}

def _err_dict(e: Exception) -> Dict[str, Any]:
    return {"prob": None, "label": None, "thr": None, "valid": False, "error": f"{type(e).__name__}: {e}"}

def _ensure_ir_loaded_safely() -> bool:
    global _IR_READY
    if _IR_READY: return True
    with _LOCK:
        if _IR_READY: return True
        try:
            logger.info(f"[MODEL] IR path={IR_PATH} exists={IR_PATH.exists()}")
            if not IR_PATH.exists():
                logger.warning(f"[MODEL] IR model not found: {IR_PATH}")
                _IR_READY = False; return False
            ir_runtime.load_ir_model(str(IR_PATH))
            _IR_READY = True
            logger.info("[MODEL] IR loaded.")
            return True
        except Exception:
            logger.exception("IR model load failed (non-fatal)")
            _IR_READY = False
            return False

def predict_all(ppg_green, ppg_ir, uuid: Optional[str] = None) -> Dict[str, Dict[str, Any]]:
    results: Dict[str, Dict[str, Any]] = {
        KEY_IR:   {"prob": None, "label": None, "thr": None, "valid": False, "error": "ir_not_loaded"},
        KEY_WEAR: {"prob": None, "label": None, "thr": None, "valid": False, "error": None},
    }

    # WEAR
    try:
        from .wear_runtime import wear_green_to_pred
        wr = importlib.import_module(f"{__package__}.wear_runtime")
        logger.info(f"[WEAR_PROBE] imported wear_runtime from {getattr(wr, '__file__', '?')}")
        results[KEY_WEAR] = wear_green_to_pred(ppg_green)
    except Exception as e:
        results[KEY_WEAR] = {"prob": None, "label": None, "thr": None, "valid": False, "error": f"wear_runtime_import: {e}"}

    # IR
    try:
        ir_ok = _ensure_ir_loaded_safely()
        logger.info("[IR_CALL] ir_ok=%s ir_is_none=%s ir_len=%s",
                    ir_ok, ppg_ir is None, (len(ppg_ir) if ppg_ir is not None else None))
        if ir_ok and (ppg_ir is not None):
            ir = ir_runtime.predict_chunk(uuid or "_legacy_", ppg_ir, sr=ir_runtime.SR)
            results[KEY_IR] = {"prob": ir.get("prob"), "label": ir.get("label"), "thr": ir.get("thr"),
                               "valid": ir.get("valid", False),
                               "error": None if ir.get("valid") else ir.get("error")}
        elif ppg_ir is None:
            results[KEY_IR] = {"prob": None, "label": None, "thr": None, "valid": False, "error": "ppg_ir is None"}
        else:
            results[KEY_IR] = {"prob": None, "label": None, "thr": None, "valid": False, "error": "ir_not_loaded"}
    except Exception as e:
        results[KEY_IR] = _err_dict(e)

    return results