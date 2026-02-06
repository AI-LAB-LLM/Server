import { setItems, appendRFromItems, appendIrFromItems } from './state.js';
import { fetchRecordsWithPulses, fetchEventStatus, startIrBaselineSession } from './api.js';
import { renderRratio, renderIrHolding } from './charts.js';

let isFetching = false;
const POLL_MS = 4000;
const DEVICE_ID = window.DASH_DEVICE_ID || null;

// =========================
// Kakao Map (Korea default)
// =========================
let kmap = null;
let kmarker = null;

// 서울시청(기본)
const FALLBACK_LAT = 37.5665;
const FALLBACK_LON = 126.9780;

// 한국 대략 범위 체크(좌표 뒤바뀜/쓰레기값 방지)
function isValidKoreaLatLon(lat, lon) {
  return (
    Number.isFinite(lat) && Number.isFinite(lon) &&
    lat >= 33 && lat <= 39 &&
    lon >= 124 && lon <= 132
  );
}

// 카카오맵 초기화 + 업데이트
function ensureKakaoMap(lat, lon) {
  const y = Number(lat);
  const x = Number(lon);

  const useLat = isValidKoreaLatLon(y, x) ? y : FALLBACK_LAT;
  const useLon = isValidKoreaLatLon(y, x) ? x : FALLBACK_LON;

  // SDK 로드 전이면 종료
  if (!window.kakao || !window.kakao.maps) return;

  // 최초 1회: 지도 생성
  if (!kmap) {
    window.kakao.maps.load(() => {
      const container = document.getElementById("map");
      if (!container) return;

      const center = new kakao.maps.LatLng(useLat, useLon);

      kmap = new kakao.maps.Map(container, {
        center,
        level: 3, // 숫자 작을수록 확대(3~5 추천)
      });

      kmarker = new kakao.maps.Marker({ position: center });
      kmarker.setMap(kmap);
    });
    return;
  }

  // 이후: 중심/마커만 갱신
  const pos = new kakao.maps.LatLng(useLat, useLon);
  kmap.setCenter(pos);
  if (kmarker) kmarker.setPosition(pos);
}

function renderAll(items) {
  setItems(items || []);
  appendRFromItems(items || []);
  appendIrFromItems(items || []);
  renderRratio();
  renderIrHolding();
}

async function fetchAndRender() {
  if (isFetching) return;
  isFetching = true;

  try {
    // 1) PPG records (device_id 필터)
    const items = await fetchRecordsWithPulses({ deviceId: DEVICE_ID, limit: 120 });
    renderAll(items);

    // 2) Event status: IMU + location
    if (DEVICE_ID) {
      const st = await fetchEventStatus(DEVICE_ID);

      // IMU 표시
      const imuEl = document.getElementById('imuLevelText');
      const tsEl  = document.getElementById('imuTs');

      if (st?.ok) {
        if (imuEl) imuEl.textContent = st.imu_display ?? '안정';
        if (tsEl) tsEl.textContent = st.timestamp ? `(${st.timestamp})` : '';

        // 지도 업데이트(좌표 없으면 서울로)
        ensureKakaoMap(st.latitude, st.longitude);
      }
    }
  } catch (e) {
    console.error('fetchAndRender error:', e);
  } finally {
    isFetching = false;
  }
}

export function startPolling() {
  if (window.__pollStarted) return;
  window.__pollStarted = true;
  fetchAndRender();
  setInterval(fetchAndRender, POLL_MS);
}

// 초기화
document.addEventListener('DOMContentLoaded', async () => {
  // 지도는 좌표 없어도 서울로 먼저 띄우기
  ensureKakaoMap(null, null);

  // 버튼(측정 시작) — 팝업 제거, 서버에 baseline만 요청
  const btn = document.getElementById('btnStartNew');
  btn?.addEventListener('click', async() => {
    const startedAt = Date.now();
    const deviceId = DEVICE_ID || '_default_';
    try {
      await startIrBaselineSession(deviceId, startedAt);
    } catch (e) {
      console.warn('[baseline] start failed', e);
    }
  });

  // 서버 렌더 초기값 반영
  const INIT = Array.isArray(window.ITEMS) ? window.ITEMS : [];
  renderAll(INIT);

  // 폴링 시작
  startPolling();
});
