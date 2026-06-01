// apnea/static/apnea/js/main.js
// 기존 ppg 앱의 main.js와 동일한 구조.
// Baseline 진행률 UI가 추가됨.

import { setItems, appendRFromItems, appendIrFromItems,resetIrbuf } from './state.js';
import { fetchRecordsWithPulses, startBaselineSession } from './api.js';
import { renderRratio, renderIrHolding, renderWearStatus } from './charts.js';


let isFetching = false;
const POLL_MS = 4000;

async function fetchAndRender() {
  if (isFetching) return;
  isFetching = true;

  try {
    const items = await fetchRecordsWithPulses();
    setItems(items);
    appendRFromItems(items);
    appendIrFromItems(items);
    renderRratio();
    renderIrHolding();
    renderWearStatus();
    updateKpi(items);
    updateBaselineUI(items);
  } catch (e) {
    console.error('fetchAndRender error:', e);
  } finally {
    isFetching = false;
  }
}

// apnea/static/apnea/js/main.js 의 updateKpi 함수만 수정

function updateKpi(items) {
    const elDev = document.getElementById('kpiDevice');
    const elBsl = document.getElementById('kpiBaseline');

    if (items.length) {
        const lastDeviceId = items[items.length - 1].device_id;
        window.__lastDeviceId = lastDeviceId;
        if (elDev) elDev.textContent = lastDeviceId;
    } else {
        if (elDev) elDev.textContent = '-';
    }

    if (elBsl) {
        const hasInference =items.some(it => !it.is_baseline && it.predictions?.APNEA_RESULT)


        if (hasInference) {
            elBsl.textContent = '✅ Ready';
            return;
        }

        // ★ 세션 시작 이후 baseline 청크만 카운트
        // window.__sessionStartTime 이후 is_baseline=True인 것만
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
}

function updateBaselineUI(items) {
  const fill = document.getElementById('baselineProgressFill');
  const text = document.getElementById('baselineProgressText');
  const stats = document.getElementById('baselineStats');
  if (!fill || !text) return;

  // baseline 완료 여부: inference 단계 레코드가 있으면 완료
  const inferenceItems = items.filter(it => !it.is_baseline && it.predictions?.IR_HOLDING);
  const baselineItems  = items.filter(it => it.is_baseline);
  const TOTAL_BASELINE = 8;

  if (inferenceItems.length > 0) {
    fill.style.width = '100%';
    fill.style.background = '#10b981';
    text.textContent = `Baseline complete (${baselineItems.length} chunks collected)`;

    const last = inferenceItems[inferenceItems.length - 1];
    const pred = last?.predictions?.IR_HOLDING;
    if (pred && stats) {
      stats.innerHTML = `
        <b>Last inference:</b><br>
        p(apnea)=${pred.prob != null ? Number(pred.prob).toFixed(3) : '-'}<br>
        label=${pred.label ?? '-'} / status=${pred.status ?? '-'}
      `;
    }
  } else {
    const pct = Math.min(100, Math.round((baselineItems.length / TOTAL_BASELINE) * 100));
    fill.style.width = pct + '%';
    fill.style.background = '#3b82f6';
    text.textContent = `Collecting baseline... ${baselineItems.length}/${TOTAL_BASELINE} chunks (${pct}%)`;
    if (stats) stats.innerHTML = '';
  }
}

export function startPolling() {
  if (window.__apneaPollStarted) return;
  window.__apneaPollStarted = true;
  fetchAndRender();
  setInterval(fetchAndRender, POLL_MS);
}

// 초기화
document.addEventListener('DOMContentLoaded', async () => {
  // ★ 모델 threshold 먼저 로드
  try {
    const { fetchModelStatus } = await import('./api.js');
    const status = await fetchModelStatus();
    const thr = status?.model_config?.threshold;
    if (thr && Number.isFinite(thr)) {
      window.__apneaThr = thr;
      console.log('[config] threshold:', thr);
    }
  } catch (e) {
    console.warn('[config] threshold load failed:', e);
  }

  // "측정 시작" 버튼
  const btn = document.getElementById('btnStartNew');
  btn?.addEventListener('click', async () => {
    const deviceId = DEVICE_ID || window.__lastDeviceId || '_default_';
    try {
      await startBaselineSession(deviceId, Date.now());
      window.__sessionStartTime = new Date();
      resetIrbuf();        // ← IRBUF 리셋
      openBaselinePopup(96);
    } catch (e) {
      console.warn('[baseline] start failed', e);
    }
  });

  // 초기 렌더
  try {
    const items = await fetchRecordsWithPulses();
    setItems(items);
    appendRFromItems(items);
    appendIrFromItems(items);
    renderRratio();
    renderIrHolding();
    renderWearStatus();
    updateKpi(items);
    updateBaselineUI(items);
  } catch (e) {
    console.warn('initial fetch error', e);
  }

  startPolling();
});

// Baseline popup (기존 wear popup 재활용)
function openBaselinePopup(totalSec) {
  const popup   = document.getElementById('wearPopup');
  const overlay = document.getElementById('wearOverlay');
  const bar     = document.getElementById('popupBar');
  const counter = document.getElementById('popupCounter');
  const status  = document.getElementById('popupStatus');
  const text    = document.getElementById('popupText');
  const closeBtn= document.getElementById('wearPopupClose');
  if (!popup) return;

  popup.classList.remove('wear-popup--hidden');
  overlay.classList.add('wear-overlay--visible');
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
    overlay.classList.remove('wear-overlay--visible');
    document.body.classList.remove('modal-open');
  }

  closeBtn?.addEventListener('click', closePopup, { once: true });
  overlay?.addEventListener('click', closePopup, { once: true });
}