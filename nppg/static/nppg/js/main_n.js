/**
 * main_n.js — 전체 대시보드(dashboard.html)용
 * 기존 ppg main.js와 동일한 구조.
 * API 경로만 /nppg/api로 변경됩니다.
 */

import { setItems, appendRFromItems, appendIrFromItems } from './state_n.js';
import { fetchRecordsWithPulses, startBaselineSession, getBaselineStatus } from './api_n.js';
import { initWearPopup } from './popup_n.js';
import { renderRratio, renderIrHolding, renderWearStatus } from './charts_n.js';

let isFetching = false;
const POLL_MS  = 4000;

function renderAll(items) {
  setItems(items || []);
  appendRFromItems(items || []);
  appendIrFromItems(items || []);

  const elDev = document.getElementById('kpiDevice');
  if (elDev && items?.length) elDev.textContent = items[items.length - 1].device_id;

  renderRratio();
  renderIrHolding();
  renderWearStatus();
}

async function fetchAndRender() {
  if (isFetching) return;
  isFetching = true;
  try {
    const items = await fetchRecordsWithPulses({ limit: 120 });
    renderAll(items);
  } catch (e) {
    console.error('[nppg] fetchAndRender error:', e);
  } finally {
    isFetching = false;
  }
}

async function pollBaselineStatus(deviceId) {
  try {
    const st   = await getBaselineStatus(deviceId);
    const bar  = document.getElementById('baselineBar');
    const meta = document.getElementById('baselineMeta');
    if (!bar || !meta) return;

    if (!st.active && !st.frozen) {
      bar.style.width  = '0%';
      meta.textContent = '측정 전';
    } else if (st.active) {
      const pct = Math.min((st.elapsed / st.total) * 100, 100).toFixed(0);
      bar.style.width  = `${pct}%`;
      meta.textContent = `베이스라인 측정 중... ${st.elapsed}s / ${st.total}s`;
    } else {
      bar.style.width  = '100%';
      meta.textContent = '베이스라인 완료 — 추론 중';
    }
  } catch (e) {
    console.warn('[nppg] baseline poll failed:', e);
  }
}

document.addEventListener('DOMContentLoaded', async () => {
  const popup = initWearPopup();

  const btn = document.getElementById('btnStartNew');
  btn?.addEventListener('click', async () => {
    const items    = Array.isArray(window.ITEMS) ? window.ITEMS : [];
    const deviceId = items.length ? items[items.length - 1].device_id : '_default_';

    try {
      await startBaselineSession(deviceId);
    } catch (e) {
      console.warn('[nppg] baseline start failed:', e);
    }

    popup?.openStartSession(Date.now());

    const bar  = document.getElementById('baselineBar');
    const meta = document.getElementById('baselineMeta');
    if (bar)  bar.style.width  = '0%';
    if (meta) meta.textContent = '베이스라인 측정 중... 0s / 90s';
  });

  const INIT = Array.isArray(window.ITEMS) ? window.ITEMS : [];
  renderAll(INIT);

  try {
    const fresh = await fetchRecordsWithPulses({ limit: 120 });
    renderAll(fresh);
    if (fresh.length) await pollBaselineStatus(fresh[fresh.length - 1].device_id);
  } catch (e) {
    console.warn('[nppg] initial fetch error:', e);
  }

  if (!window.__nppgPollStarted) {
    window.__nppgPollStarted = true;
    setInterval(fetchAndRender, POLL_MS);
    setInterval(async () => {
      const items = Array.isArray(window.ITEMS) ? window.ITEMS : [];
      if (items.length) await pollBaselineStatus(items[items.length - 1].device_id);
    }, POLL_MS);
  }
});