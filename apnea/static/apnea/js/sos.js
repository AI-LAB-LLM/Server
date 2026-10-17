// apnea/static/apnea/js/sos.js

const POLL_MS = 3000;

const RULES = {
  'physical+psychological': '지속적인 물리 및 심리적 이상',
  'psychological': '지속적인 심리적 이상',
};

let current = null;
let elapsedBase = 0;
let fetchedAt = 0;
let fetching = false;
let closing = false;

const $ = id => document.getElementById(id);

const pad = n => String(n).padStart(2, '0');

function formatElapsed(seconds) {
  const total = Math.max(0, Math.floor(seconds));

  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  const secs = total % 60;

  if (hours > 0) {
    return `${hours}:${pad(minutes)}:${pad(secs)}`;
  }

  return `${pad(minutes)}:${pad(secs)}`;
}

function formatTime(iso) {
  if (!iso) return '-';

  const d = new Date(iso);

  if (Number.isNaN(d.getTime())) return '-';

  return d.toLocaleTimeString('ko-KR', {
    hour12: false,
  });
}

function getBasis(sos) {
  if (sos.rule === 'physical+psychological') {
    return {
      main: RULES[sos.rule],
      sub:
        `30초 내 강한 움직임 연속 ${sos.imu_streak}회` +
        ` · 심리적 위협 ${sos.ppg_true}회` +
        ` · IMU 최대 레벨 ${sos.imu_max_level ?? '-'}`,
    };
  }

  if (sos.rule === 'psychological') {
    return {
      main: RULES[sos.rule],
      sub: `30초 내 심리적 위협 ${sos.ppg_true}회`,
    };
  }

  return {
    main: 'SOS 발생',
    sub: '판단 근거를 확인할 수 없습니다.',
  };
}

function tick() {
  if (!current) return;

  const elapsed = (
    elapsedBase +
    (performance.now() - fetchedAt) / 1000
  );

  const el = $('sosElapsed');

  if (el) {
    el.textContent = formatElapsed(elapsed);
  }
}

function show(sos) {
  const banner = $('sosBanner');

  if (!banner) return;

  if (!sos) {
    current = null;
    banner.hidden = true;
    return;
  }

  current = sos;

  elapsedBase = Number(sos.elapsed_sec) || 0;
  fetchedAt = performance.now();

  const basis = getBasis(sos);

  $('sosBasisMain').textContent = basis.main;
  $('sosBasisSub').textContent = basis.sub;

  $('sosOccurred').textContent = formatTime(
    sos.occurred_at
  );

  tick();

  banner.hidden = false;
}

async function poll(deviceId) {
  if (fetching || closing) return;

  fetching = true;

  try {
    const url = (
      `/apnea/api/sos/status/` +
      `?device_id=${encodeURIComponent(deviceId)}` +
      `&t=${Date.now()}`
    );

    const res = await fetch(url, {
      cache: 'no-store',
    });

    if (!res.ok) {
      throw new Error(`HTTP ${res.status}`);
    }

    const data = await res.json();

    if (data.ok) {
      show(data.active ? data.sos : null);
    }

  } catch (error) {
    console.warn('[SOS] 조회 실패:', error);
  } finally {
    fetching = false;
  }
}

async function closeIncident(deviceId) {
  if (!current || closing) return;

  const confirmed = confirm(
    '사건을 종결하시겠습니까?\n' +
    '종결하면 SOS 경고가 해제됩니다.'
  );

  if (!confirmed) return;

  const btn = $('sosCloseBtn');

  closing = true;
  btn.disabled = true;

  try {
    const res = await fetch(
      '/apnea/api/sos/close/',
      {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          device_id: deviceId,
          incident_id: current.incident_id,
        }),
      }
    );

    const data = await res.json();

    if (!res.ok || !data.ok) {
      throw new Error(
        data.error || `HTTP ${res.status}`
      );
    }

    show(null);

  } catch (error) {
    alert(
      `사건 종결에 실패했습니다: ${error.message}`
    );
  } finally {
    btn.disabled = false;
    closing = false;
  }
}

export function startSosMonitor(deviceId) {
  if (
    !deviceId ||
    window.__sosPollStarted
  ) {
    return;
  }

  window.__sosPollStarted = true;

  $('sosCloseBtn')?.addEventListener(
    'click',
    () => closeIncident(deviceId)
  );

  poll(deviceId);

  setInterval(
    () => poll(deviceId),
    POLL_MS
  );

  setInterval(tick, 1000);
}