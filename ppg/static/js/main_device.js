import { setItems, appendRFromItems, appendIrFromItems } from './state.js';
import { fetchRecordsWithPulses, fetchEventStatus, startIrBaselineSession } from './api.js';
import { renderRratio, renderIrHolding } from './charts.js';
import { startImuMonitor } from './imu.js';
import { startSosMonitor } from './sos.js';

let isFetching = false;
const POLL_MS = 4000;
const DEVICE_ID = window.DASH_DEVICE_ID || null;





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


