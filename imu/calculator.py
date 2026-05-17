import json
from collections import deque
from pathlib import Path
from typing import Dict, List, Tuple
import numpy as np
from django.conf import settings
try:
    from tflite_runtime.interpreter import Interpreter
except ImportError:
    from tensorflow.lite.python.interpreter import Interpreter


MODEL_DIR = Path(settings.MEDIA_ROOT) / "models" / "imu"

MODEL_PATH = MODEL_DIR / "model_classification.tflite"
SCALER_PATH = MODEL_DIR / "scaler.json"

EXPECTED_SAMPLE_RATE = 25
EXPECTED_WINDOW_SEC = 12
EXPECTED_SAMPLE_COUNT = EXPECTED_SAMPLE_RATE * EXPECTED_WINDOW_SEC

NUM_CLASSES = 5
MODEL_OUTPUT_IS_PROBS = True

TH_P5 = 0.90
TH_P4 = 0.70
MARGIN_P5 = 0.30
MARGIN_P4 = 0.18
GATE_Z_STD = 0.80
GATE_Z_DSTD = 0.90

TH_ACTIVITY = 9.90
TH_D_ACTIVITY = 0.15
TH_ACTIVE_STD = 1.0
TH_ACTIVE_DSTD = 1.2

STRONG_P3 = 0.65
STRONG_MARGIN_P3 = 0.15
MOD_STD_MIN = 0.30
MOD_DSTD_MIN = 0.30

HYST_UP_CONSEC = 2
HYST_DOWN_CONSEC = 1
TH_P3_UP = 0.45
TH_P3_DOWN = 0.45
MARGIN_P3_UP = 0.10
MARGIN_P3_DOWN = 0.05

SHAKE_DSTD_HIGH = 1.60
SHAKE_MEAN_MAX = TH_ACTIVITY + 0.8
P3_STRONG_MIN = 0.60
JERK_RATIO_TH = 1.30


class ImuState:
    def __init__(self):
        self.recent_p = deque(maxlen=2)
        self.recent_x = deque(maxlen=3)
        self.recent_grade_cand = deque(maxlen=3)
        self.consec3_cond = 0
        self.consec2_cond = 0
        self.state23 = 2


_STATE_BY_PROTECTEE: Dict[int, ImuState] = {}


def get_state(protectee_id: int) -> ImuState:
    if protectee_id not in _STATE_BY_PROTECTEE:
        _STATE_BY_PROTECTEE[protectee_id] = ImuState()
    return _STATE_BY_PROTECTEE[protectee_id]


_interpreter = None
_input_details = None
_output_details = None
_mu4 = None
_sigma4 = None


def load_scaler() -> Tuple[np.ndarray, np.ndarray]:
    global _mu4, _sigma4

    if _mu4 is not None and _sigma4 is not None:
        return _mu4, _sigma4

    if not SCALER_PATH.exists():
        raise FileNotFoundError(f"scaler.json 파일이 없습니다: {SCALER_PATH}")

    with open(SCALER_PATH, "r", encoding="utf-8") as f:
        obj = json.load(f)

    mean = obj.get("mean_", obj.get("mean"))
    scale = obj.get("scale_", obj.get("scale"))

    if mean is None or scale is None:
        raise ValueError("scaler.json 안에 mean_/scale_ 또는 mean/scale 키가 필요합니다.")

    _mu4 = np.array(mean, dtype=np.float32)
    _sigma4 = np.array(scale, dtype=np.float32)

    if _mu4.shape[0] != 4 or _sigma4.shape[0] != 4:
        raise ValueError("scaler mean/scale은 길이 4여야 합니다.")

    return _mu4, _sigma4


def load_interpreter():
    global _interpreter, _input_details, _output_details

    if _interpreter is not None:
        return _interpreter, _input_details, _output_details

    if not MODEL_PATH.exists():
        raise FileNotFoundError(f"TFLite 모델 파일이 없습니다: {MODEL_PATH}")

    interpreter = Interpreter(model_path=str(MODEL_PATH), num_threads=4)
    interpreter.allocate_tensors()

    _interpreter = interpreter
    _input_details = interpreter.get_input_details()
    _output_details = interpreter.get_output_details()

    return _interpreter, _input_details, _output_details


# 수학 유틸
def softmax(logits: np.ndarray) -> np.ndarray:
    logits = logits.astype(np.float32)
    m = np.max(logits)
    exps = np.exp(logits - m)
    total = np.sum(exps)

    if total == 0:
        return exps

    return exps / total


def mean_of_deque(q: deque) -> np.ndarray:
    if not q:
        return np.array([], dtype=np.float32)

    return np.mean(np.stack(list(q), axis=0), axis=0).astype(np.float32)


def median3(a: int, b: int, c: int) -> int:
    return sorted([a, b, c])[1]


def extract_features(samples: List[List[float]]) -> np.ndarray:
    """
    samples: [[x, y, z], ...] 길이 300

    현재 서버 기준:
    - sample_rate = 25Hz
    - window_sec = 12초
    - sample_count = 300개

    return:
    [svm_mean, svm_std, d_svm_mean, d_svm_std]
    """
    arr = np.array(samples, dtype=np.float32)

    if arr.ndim != 2 or arr.shape[1] != 3:
        raise ValueError(f"samples는 shape (N, 3)이어야 합니다. 현재 shape={arr.shape}")

    if arr.shape[0] != EXPECTED_SAMPLE_COUNT:
        raise ValueError(
            f"samples 길이는 반드시 {EXPECTED_SAMPLE_COUNT}개여야 합니다. "
            f"현재 {arr.shape[0]}개입니다. "
            f"기준: {EXPECTED_SAMPLE_RATE}Hz * {EXPECTED_WINDOW_SEC}초"
        )

    svm = np.sqrt(np.sum(arr * arr, axis=1))
    dsvm = np.abs(np.diff(svm))

    x1 = float(np.mean(svm))
    x2 = float(np.std(svm, ddof=0))
    x3 = float(np.mean(dsvm))
    x4 = float(np.std(dsvm, ddof=0))

    return np.array([x1, x2, x3, x4], dtype=np.float32)


def predict_probs(raw4: np.ndarray) -> np.ndarray:
    mu4, sigma4 = load_scaler()
    interpreter, input_details, output_details = load_interpreter()

    safe_sigma = np.where(sigma4 == 0, 1.0, sigma4)
    norm4 = ((raw4 - mu4) / safe_sigma).astype(np.float32)

    input_data = np.expand_dims(norm4, axis=0).astype(np.float32)

    input_index = input_details[0]["index"]
    output_index = output_details[0]["index"]

    interpreter.set_tensor(input_index, input_data)
    interpreter.invoke()

    out = interpreter.get_tensor(output_index)[0].astype(np.float32)

    if MODEL_OUTPUT_IS_PROBS:
        return out

    return softmax(out)


def calculate_grade_from_probs(
    protectee_id: int,
    raw4: np.ndarray,
    probs: np.ndarray,
) -> int:
    state = get_state(protectee_id)
    mu4, sigma4 = load_scaler()

    x1, x2, x3, x4 = [float(v) for v in raw4]

    p1 = float(probs[0]) if len(probs) > 0 else 0.0
    p2 = float(probs[1]) if len(probs) > 1 else 0.0
    p3c = float(probs[2]) if len(probs) > 2 else 0.0
    p4 = float(probs[3]) if len(probs) > 3 else 0.0
    p5 = float(probs[4]) if len(probs) > 4 else 0.0

    z_std = (x2 - float(mu4[1])) / (float(sigma4[1]) if float(sigma4[1]) != 0 else 1.0)
    z_dstd = (x4 - float(mu4[3])) / (float(sigma4[3]) if float(sigma4[3]) != 0 else 1.0)

    allow_high = (abs(z_std) >= GATE_Z_STD) or (abs(z_dstd) >= GATE_Z_DSTD)

    is_static = (x1 < TH_ACTIVITY) and (x3 < TH_D_ACTIVITY)
    is_active_any = (x2 >= TH_ACTIVE_STD) or (x4 >= TH_ACTIVE_DSTD)

    state.recent_p.append(np.array([p1, p2, p3c, p4, p5], dtype=np.float32))
    state.recent_x.append(np.array([x1, x2, x3, x4], dtype=np.float32))

    p_mean = mean_of_deque(state.recent_p)
    x_mean = mean_of_deque(state.recent_x)

    mp1 = float(p_mean[0]) if len(p_mean) > 0 else p1
    mp2 = float(p_mean[1]) if len(p_mean) > 1 else p2
    mp3 = float(p_mean[2]) if len(p_mean) > 2 else p3c

    mx1 = float(x_mean[0]) if len(x_mean) > 0 else x1
    mx2 = float(x_mean[1]) if len(x_mean) > 1 else x2
    mx4 = float(x_mean[3]) if len(x_mean) > 3 else x4

    max_low = max(mp1, mp2)

    allow3_up = (mp3 >= TH_P3_UP) and ((mp3 - max_low) >= MARGIN_P3_UP)
    allow3_down = (mp3 < TH_P3_DOWN) or ((mp3 - max_low) < MARGIN_P3_DOWN)

    jerk_ratio = mx4 / (mx2 + 1e-6)
    is_jerk_like = (
        (x4 >= SHAKE_DSTD_HIGH or mx4 >= SHAKE_DSTD_HIGH)
        and (x1 <= SHAKE_MEAN_MAX and mx1 <= SHAKE_MEAN_MAX)
        and (p3c < P3_STRONG_MIN and mp3 < P3_STRONG_MIN)
        and (jerk_ratio >= JERK_RATIO_TH)
    )

    grade = None

    # 4, 5는 최우선
    if allow_high and (p5 >= TH_P5) and ((p5 - p3c) >= MARGIN_P5):
        grade = 5
    elif allow_high and (p4 >= TH_P4) and ((p4 - p3c) >= MARGIN_P4):
        grade = 4

    if grade is None:
        if is_static:
            state.recent_grade_cand.append(1)
            grade = 1
        else:
            in_moderate_band = (
                MOD_STD_MIN <= x2 <= (TH_ACTIVE_STD - 1e-6)
                and MOD_DSTD_MIN <= x4 <= (TH_ACTIVE_DSTD - 1e-6)
            )

            if is_jerk_like:
                state.consec3_cond = 0
                state.consec2_cond += 1

                if state.consec2_cond >= HYST_DOWN_CONSEC:
                    state.state23 = 2
            else:
                if allow3_up and is_active_any:
                    state.consec3_cond += 1
                    state.consec2_cond = 0

                    if state.consec3_cond >= HYST_UP_CONSEC:
                        state.state23 = 3

                elif allow3_down or in_moderate_band:
                    state.consec2_cond += 1
                    state.consec3_cond = 0

                    if state.consec2_cond >= HYST_DOWN_CONSEC:
                        state.state23 = 2

                else:
                    state.consec3_cond = 0
                    state.consec2_cond = 0

            state.recent_grade_cand.append(state.state23)

            if len(state.recent_grade_cand) == 3:
                g_cand = median3(
                    state.recent_grade_cand[0],
                    state.recent_grade_cand[1],
                    state.recent_grade_cand[2],
                )
            else:
                g_cand = state.state23

            grade = min(g_cand, 3)

            strong3 = (mp3 >= STRONG_P3) and ((mp3 - max_low) >= STRONG_MARGIN_P3)

            if grade == 3 and in_moderate_band and not strong3:
                grade = 2

    return int(max(1, min(5, grade)))


def calculate_imu_level(protectee_id: int, samples: List[List[float]]) -> dict:
    """
    외부에서 호출할 메인 함수.

    입력 기준:
    - 25Hz
    - 12초
    - 300 samples

    return 예:
    {
        "level": 2,
        "features": {...},
        "probs": [...]
    }
    """
    raw4 = extract_features(samples)
    probs = predict_probs(raw4)
    level = calculate_grade_from_probs(protectee_id, raw4, probs)

    return {
        "level": level,
        "sample_rate": EXPECTED_SAMPLE_RATE,
        "window_sec": EXPECTED_WINDOW_SEC,
        "sample_count": EXPECTED_SAMPLE_COUNT,
        "features": {
            "svm_mean": float(raw4[0]),
            "svm_std": float(raw4[1]),
            "d_svm_mean": float(raw4[2]),
            "d_svm_std": float(raw4[3]),
        },
        "probs": [float(v) for v in probs],
    }