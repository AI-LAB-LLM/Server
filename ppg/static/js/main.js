import { setItems, appendRFromItems, appendIrFromItems } from './state.js';
import { fetchRecordsWithPulses, startIrBaselineSession } from './api.js';
import { initWearPopup } from './popup.js';
import { renderRratio, renderIrHolding, renderWearStatus } from './charts.js';

let isFetching = false;   // 중복 호출 방지
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
  } catch (e) {
    console.error('fetchAndRender error:', e);
  } finally {
    isFetching = false;
  }
}

export function startPolling() {
  if (window.__pollStarted) return;
  window.__pollStarted = true;
  fetchAndRender();                      // 즉시 한 번 실행
  setInterval(fetchAndRender, POLL_MS);  // 이후 주기적 갱신
}

function renderAll(items) {
  setItems(items || []);
  appendRFromItems(items || []);
  appendIrFromItems(items || []);

  // KPI
  const elRec = document.getElementById('kpiRecords');
  const elDev = document.getElementById('kpiDevice');
  const ITEMS = items || [];
  if (elRec) elRec.textContent = ITEMS.length;
  if (elDev) elDev.textContent = ITEMS.length ? ITEMS[ITEMS.length - 1].device_id : '-';

  // 차트
  renderRratio();
  renderIrHolding();
  renderWearStatus();

  const last = ITEMS[ITEMS.length - 1];
  if (last && popup?.onNewRecord) popup.onNewRecord(last);
}

function setWearCard(statusText, metaText, className) {
  const card  = document.getElementById('wearCard');
  const elTxt = document.getElementById('wearStatusText');
  const elMeta= document.getElementById('wearStatusMeta');
  if (!card || !elTxt || !elMeta) return;

  card.classList.remove('is-wear','is-off','is-unk');
  if (className) card.classList.add(className);
  elTxt.textContent  = statusText ?? '-';
  elMeta.textContent = metaText ?? '';
}

// 초기화
let popup = null;
document.addEventListener('DOMContentLoaded', async () => {
  popup = initWearPopup();

  const btn = document.getElementById('btnStartNew');
  btn?.addEventListener('click', async() => {
    const startedAt = Date.now();
    const deviceId = (Array.isArray(window.ITEMS) && window.ITEMS.length)
    ? window.ITEMS[window.ITEMS.length - 1].device_id
    : '_default_';

    try {
      await startIrBaselineSession(deviceId, startedAt);
    } catch (e) {
      console.warn('[baseline] start failed', e);
    }

    popup?.openStartSession(Date.now());

    const card  = document.getElementById('wearCard');
    const elTxt = document.getElementById('wearStatusText');
    const elMeta= document.getElementById('wearStatusMeta');
    if (card && elTxt && elMeta) {
      card.classList.remove('is-wear','is-off');
      card.classList.add('is-unk');
      elTxt.textContent  = 'Still checking...';
      elMeta.textContent = '착용 직후 – 판정 대기 중';
    }
  });

  const INIT = (typeof window !== 'undefined' && Array.isArray(window.ITEMS)) ? window.ITEMS : [];
  renderAll(INIT);

  // 첫 갱신
  try {
    const fresh = await fetchRecordsWithPulses();
    renderAll(fresh);
  } catch (e) {
    console.warn('initial fetch error', e);
  }

  startPolling();
});