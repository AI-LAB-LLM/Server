import { setItems, appendRFromItems, appendIrFromItems } from './state.js';
import { fetchRecordsWithPulses, startBaselineSession, fetchModelStatus } from './api.js';
import { renderRratio, renderIrHolding, renderWearStatus } from './charts.js';

let isFetching = false;
const POLL_MS   = 4000;
const DEVICE_ID = window.DASH_DEVICE_ID || null;

// ── 카카오맵 ──────────────────────────────────
let kmap = null, kmarker = null;

function isValidKoreaLatLon(lat, lon) {
  return Number.isFinite(lat) && Number.isFinite(lon) &&
         lat >= 33 && lat <= 39 && lon >= 124 && lon <= 132;
}

function ensureKakaoMap(lat, lon) {
  if (!window.kakao || !window.kakao.maps) return;
  const y = Number(lat), x = Number(lon);
  const useLat = isValidKoreaLatLon(y, x) ? y : 37.5665;
  const useLon = isValidKoreaLatLon(y, x) ? x : 126.9780;

  if (!kmap) {
    window.kakao.maps.load(() => {
      const container = document.getElementById('map');
      if (!container) return;
      const center = new kakao.maps.LatLng(useLat, useLon);
      kmap    = new kakao.maps.Map(container, { center, level: 3 });
      kmarker = new kakao.maps.Marker({ position: center });
      kmarker.setMap(kmap);
    });
    return;
  }
  const pos = new kakao.maps.LatLng(useLat, useLon);
  kmap.setCenter(pos);
  if (kmarker) kmarker.setPosition(pos);
}

// ── KPI ───────────────────────────────────────
function updateKpi(items) {
  const elBsl = document.getElementById('kpiBaseline');
  if (!elBsl) return;

  const hasInference = items.some(it => !it.is_baseline && it.predictions?.APNEA_RESULT);
  if (hasInference) {
    elBsl.textContent = '✅ Ready';
    return;
  }

  if (!window.__sessionStartTime) {
    elBsl.textContent = '-';
    return;
  }

  const baselineCount = items.filter(it =>
    it.is_baseline &&
    new Date(it.timestamp) >= window.__sessionStartTime
  ).length;
  elBsl.textContent = `⏳ ${baselineCount} / 8 chunks`;
}

// ── IMU ───────────────────────────────────────
function updateImu(st) {
  const imuEl    = document.getElementById('imuLevelText');
  const imuLevel = document.getElementById('imuDangerLevel');
  const tsEl     = document.getElementById('imuTs');

  if (!st?.ok) return;

  if (imuEl) imuEl.textContent = st.imu_display ?? '안정';
  if (imuLevel) {
    const lv = st.imu_danger_level;
    imuLevel.textContent = lv != null ? `위험도: ${lv}` : '';
    if (lv >= 4)      imuLevel.style.color = '#ef4444';
    else if (lv >= 2) imuLevel.style.color = '#f97316';
    else              imuLevel.style.color = '#6b7280';
  }
  if (tsEl) tsEl.textContent = st.timestamp ? `(${st.timestamp})` : '';
}

// ── Baseline 팝업 ──────────────────────────────
window.openBaselinePopup = function(totalSec) {
  const popup    = document.getElementById('wearPopup');
  const overlay  = document.getElementById('wearOverlay');
  const bar      = document.getElementById('popupBar');
  const counter  = document.getElementById('popupCounter');
  const status   = document.getElementById('popupStatus');
  const text     = document.getElementById('popupText');
  const closeBtn = document.getElementById('wearPopupClose');
  if (!popup) return;

  popup.classList.remove('wear-popup--hidden');
  if (overlay) overlay.classList.add('wear-overlay--visible');
  document.body.classList.add('modal-open');

  status.textContent = 'Collecting Baseline';
  text.textContent   = `Keep the watch on for ${totalSec}s`;

  const startMs = Date.now();
  const iv = setInterval(() => {
    const elapsed = Math.min(totalSec, (Date.now() - startMs) / 1000);
    const pct = Math.round((elapsed / totalSec) * 100);
    bar.style.width = pct + '%';
    counter.textContent = `${Math.round(elapsed)}s / ${totalSec}s`;

    if (elapsed >= totalSec) {
      clearInterval(iv);
      status.textContent = 'Baseline Complete';
      text.textContent   = 'Starting inference...';
      setTimeout(() => closePopup(), 2000);
    }
  }, 500);

  function closePopup() {
    clearInterval(iv);
    popup.classList.add('wear-popup--hidden');
    if (overlay) overlay.classList.remove('wear-overlay--visible');
    document.body.classList.remove('modal-open');
  }

  closeBtn?.addEventListener('click', closePopup, { once: true });
  overlay?.addEventListener('click', closePopup, { once: true });
}

// ── 메인 폴링 ─────────────────────────────────
async function fetchAndRender() {
  if (isFetching) return;
  isFetching = true;
  try {
    const items = await fetchRecordsWithPulses({ deviceId: DEVICE_ID, limit: 120 });
    setItems(items);
    appendRFromItems(items);
    appendIrFromItems(items);
    renderRratio();
    renderIrHolding();
    renderWearStatus();
    updateKpi(items);

    if (DEVICE_ID) {
      const res = await fetch(
        `/apnea/api/event_status/?device_id=${encodeURIComponent(DEVICE_ID)}&t=${Date.now()}`,
        { cache: 'no-store' }
      );
      if (res.ok) {
        const st = await res.json();
        updateImu(st);
        ensureKakaoMap(st.latitude, st.longitude);
      }
    }
  } catch (e) {
    console.error('fetchAndRender error:', e);
  } finally {
    isFetching = false;
  }
}

// ── 초기화 (즉시 실행) ────────────────────────
(async () => {
  try {
    const status = await fetchModelStatus();
    const thr = status?.model_config?.threshold;
    if (thr && Number.isFinite(thr)) window.__apneaThr = thr;
  } catch (e) {}

  ensureKakaoMap(null, null);

  const btn = document.getElementById('btnStartNew');
  btn?.addEventListener('click', async () => {
    const deviceId = DEVICE_ID || '_default_';
    try {
      await startBaselineSession(deviceId, Date.now());
      window.__sessionStartTime = new Date();
      openBaselinePopup(96);
    } catch (e) {
      console.warn('[baseline] start failed', e);
    }
  });

  const INIT = Array.isArray(window.ITEMS) ? window.ITEMS : [];
  setItems(INIT);
  appendRFromItems(INIT);
  appendIrFromItems(INIT);
  renderRratio();
  renderIrHolding();
  renderWearStatus();
  updateKpi(INIT);

  if (!window.__devicePollStarted) {
    window.__devicePollStarted = true;
    setInterval(fetchAndRender, POLL_MS);
    fetchAndRender();
  }
})();