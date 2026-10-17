import { setItems, appendRFromItems, appendIrFromItems, resetIrbuf } from './state.js';
import { fetchRecordsWithPulses, startBaselineSession, fetchModelStatus,fetchBaselines, selectBaseline, } from './api.js';
import { renderRratio, renderIrHolding, renderWearStatus } from './charts.js';
import { startImuMonitor } from './imu.js';
import { startSosMonitor } from './sos.js';

let isFetching = false;
const POLL_MS   = 4000;
const DEVICE_ID = window.DASH_DEVICE_ID || null;



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

//   const baselineCount = items.filter(it =>
//     it.is_baseline &&
//     new Date(it.timestamp) >= window.__sessionStartTime
//   ).length;
//   elBsl.textContent = `⏳ ${baselineCount} / 8 chunks`;
// }

  const elapsedSec = Math.min(
      30,
      Math.floor((Date.now() - window.__sessionStartTime.getTime()) / 1000)
  );

  elBsl.textContent = `⏳ ${elapsedSec} / 30초`;
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
    text.textContent = 'Starting inference...';

    setTimeout(() => {
      closePopup();

      // 서버에서 baseline 저장이 끝날 시간을 조금 기다렸다가
      // 목록을 다시 가져온다.
      setTimeout(() => {
        loadBaselineList();
      }, 3000);

    }, 2000);
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

function formatBaselineTime(iso) {
    const d = new Date(iso);

    return new Intl.DateTimeFormat(
        'ko-KR',
        {
            year: 'numeric',
            month: '2-digit',
            day: '2-digit',
            hour: '2-digit',
            minute: '2-digit',
            hour12: false,
        }
    ).format(d);
}


async function loadBaselineList() {
    const listEl =
        document.getElementById('baselineList');

    if (!listEl || !DEVICE_ID) {
        return;
    }

    try {
        const data =
            await fetchBaselines(DEVICE_ID);

        renderBaselineList(
            data.items || []
        );

    } catch (e) {
        console.error(
            '[baseline] list failed',
            e
        );

        listEl.innerHTML = `
            <div class="baseline-empty">
                베이스라인 목록을
                불러오지 못했습니다.
            </div>
        `;
    }
}


function renderBaselineList(items) {
    const listEl =
        document.getElementById('baselineList');

    if (!listEl) {
        return;
    }

    listEl.innerHTML = '';

    if (!items.length) {
        listEl.innerHTML = `
            <div class="baseline-empty">
                저장된 베이스라인이 없습니다.
            </div>
        `;

        return;
    }

    for (const item of items) {

        const row =
            document.createElement('button');

        row.type = 'button';

        row.className =
            'baseline-item' +
            (
                item.selected
                    ? ' baseline-item--selected'
                    : ''
            );

        // 동그라미
        const radio =
            document.createElement('span');

        radio.className = 'baseline-radio';

        radio.textContent =
            item.selected ? '●' : '○';


        // 오른쪽 정보
        const info =
            document.createElement('span');

        info.className = 'baseline-info';


        const time =
            document.createElement('span');

        time.className = 'baseline-time';

        time.textContent =
            formatBaselineTime(
                item.measured_at
            );


        const detail =
            document.createElement('span');

        detail.className =
            'baseline-detail';

        if (item.selected) {
            detail.textContent =
                '현재 추론에 사용 중';

        } else if (
            item.num_beats != null
        ) {
            detail.textContent =
                `${item.num_beats} beats`;

        } else {
            detail.textContent = '';
        }


        info.appendChild(time);
        info.appendChild(detail);

        row.appendChild(radio);
        row.appendChild(info);


        row.addEventListener(
            'click',
            async () => {

                if (item.selected) {
                    return;
                }

                const ok = window.confirm(
                    `${formatBaselineTime(
                        item.measured_at
                    )} 베이스라인으로 변경할까요?`
                );

                if (!ok) {
                    return;
                }

                try {
                    row.disabled = true;

                    await selectBaseline(
                        DEVICE_ID,
                        item.session_id
                    );

                    console.log(
                        '[baseline] changed:',
                        item.session_id
                    );

                    // 목록 다시 받아서
                    // ● 표시 변경
                    await loadBaselineList();

                } catch (e) {

                    console.error(
                        '[baseline] select failed',
                        e
                    );

                    alert(
                        '베이스라인 변경에 실패했습니다.'
                    );

                    row.disabled = false;
                }
            }
        );

        listEl.appendChild(row);
    }
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
    updateDelay(items);


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


  await loadBaselineList();

  const btn = document.getElementById('btnStartNew');
  btn?.addEventListener('click', async () => {
  const deviceId = DEVICE_ID || window.__lastDeviceId || '_default_';
  try {
    await startBaselineSession(deviceId, Date.now());
    window.__sessionStartTime = new Date();
    resetIrbuf();        // ← IRBUF 리셋
    window.openBaselinePopup(30);
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
  updateDelay(INIT);

  startImuMonitor(DEVICE_ID);
  startSosMonitor(DEVICE_ID);

  if (!window.__devicePollStarted) {
    window.__devicePollStarted = true;
    setInterval(fetchAndRender, POLL_MS);
    fetchAndRender();
  }
})();

function updateDelay(items) {
  const delayEl = document.getElementById('delayText');
  if (!delayEl || !items.length) return;

  const last = items[items.length - 1];
  const watchTs    = last?.timestamp;
  const receivedTs = last?.received_at;

  if (!watchTs || !receivedTs) {
    delayEl.textContent = '-';
    return;
  }

  const watchEndMs   = new Date(watchTs).getTime() + 12000;
  const receivedMs   = new Date(receivedTs).getTime();
  const diff         = (receivedMs - watchEndMs) / 1000;

  delayEl.textContent = `전송 딜레이: ${diff.toFixed(1)}초`;
  delayEl.style.color = diff > 10 ? '#ef4444' : diff > 5 ? '#f97316' : '#10b981';
}
