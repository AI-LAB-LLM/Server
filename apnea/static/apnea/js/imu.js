// apnea/static/apnea/js/imu.js

const POLL_MS = 3000;
const SLOT_SEC = 6;
const HISTORY_SEC = 180;
const SLOTS = HISTORY_SEC / SLOT_SEC;

const IMU_LEVELS = {
  1: { name: '정적', desc: '움직임이 거의 없는 상태' },
  2: { name: '저강도', desc: '가벼운 움직임' },
  3: { name: '보통', desc: '일상 활동 수준의 움직임' },
  4: { name: '고강도', desc: '급격하고 강한 움직임' },
  5: { name: '격렬·위험', desc: '매우 격렬한 움직임' },
};

const BAR_HEIGHT = {
  1: 22,
  2: 38,
  3: 55,
  4: 78,
  5: 100,
};

let fetching = false;

const $ = id => document.getElementById(id);

const validLevel = value => {
  const n = Number(value);
  return Number.isInteger(n) && IMU_LEVELS[n]
    ? n
    : null;
};

const hhmmss = iso => {
  if (!iso) return '-';

  const d = new Date(iso);
  return Number.isNaN(d.getTime())
    ? '-'
    : d.toLocaleTimeString('ko-KR', {
        hour12: false,
      });
};

function buildScale() {
  const list = $('imuScale');

  if (!list || list.childElementCount) return;

  for (const [level, meta] of Object.entries(IMU_LEVELS)) {
    const li = document.createElement('li');

    li.className = 'imu-scale__item';
    li.dataset.level = level;
    li.title = `${level} ${meta.name}: ${meta.desc}`;

    li.innerHTML = `
      <span class="imu-scale__chip"></span>
      <span class="imu-scale__name">
        ${level} ${meta.name}
      </span>
    `;

    list.appendChild(li);
  }
}

function buildBars() {
  const bars = $('imuBars');

  if (!bars || bars.childElementCount) return;

  for (let i = 0; i < SLOTS; i++) {
    const bar = document.createElement('div');

    bar.className = 'imu-bar is-empty';
    bar.innerHTML = `
      <span class="imu-bar__fill"></span>
    `;

    bars.appendChild(bar);
  }
}

function toSlots(history) {
  const slots = new Array(SLOTS).fill(null);

  const now = Date.now();

  for (const item of history) {
    const level = validLevel(item.level);

    if (level === null) continue;

    let offset;

    if (item.offset_sec != null) {
      offset = Number(item.offset_sec);
    } else {
      const eventMs = new Date(item.timestamp).getTime();
      offset = (now - eventMs) / 1000;
    }

    if (
      !Number.isFinite(offset) ||
      offset < 0 ||
      offset >= HISTORY_SEC
    ) {
      continue;
    }

    const index =
      SLOTS - 1 - Math.floor(offset / SLOT_SEC);

    if (
      !slots[index] ||
      level > slots[index].level
    ) {
      slots[index] = {
        level,
        timestamp: item.timestamp,
      };
    }
  }

  return slots;
}

function render(status) {
  const card = $('imuCard');
  if (!card) return;

  buildScale();
  buildBars();

  const level = status?.ok
    ? validLevel(status.imu_danger_level)
    : null;

  const history = Array.isArray(status?.history)
    ? status.history
    : [];

  const meta = level !== null
    ? IMU_LEVELS[level]
    : null;

  const tile = $('imuTile');

  if (level !== null) {
    tile.dataset.level = String(level);
  } else {
    delete tile.dataset.level;
  }

  $('imuTileNum').textContent = level ?? '–';

  $('imuTileLabel').textContent = meta
    ? meta.name
    : '데이터 없음';

  $('imuDesc').textContent = meta
    ? meta.desc
    : '최근 3분간 측정 결과가 없습니다.';

  $('imuTs').textContent = level !== null
    ? `측정 시각 ${hhmmss(status.timestamp)}`
    : '';

  card.classList.toggle(
    'is-threat',
    level !== null && level >= 4
  );

  const slots = toSlots(history);

  document.querySelectorAll(
    '#imuScale .imu-scale__item'
  ).forEach(li => {
    li.classList.toggle(
      'is-active',
      Number(li.dataset.level) === level
    );
  });

  document.querySelectorAll(
    '#imuBars .imu-bar'
  ).forEach((bar, index) => {
    const slot = slots[index];
    const fill = bar.firstElementChild;

    if (!slot) {
      delete bar.dataset.level;
      bar.classList.add('is-empty');
      fill.style.height = '0';
      bar.title = '데이터 없음';
      return;
    }

    bar.dataset.level = String(slot.level);
    bar.classList.remove('is-empty');

    fill.style.height = `${BAR_HEIGHT[slot.level]}%`;

    bar.title = (
      `${hhmmss(slot.timestamp)} · ` +
      `${slot.level} ${IMU_LEVELS[slot.level].name}`
    );
  });
}

async function poll(deviceId) {
  if (fetching) return;
  fetching = true;

  try {
    const url = (
      `/apnea/api/event_status/` +
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
    render(data);

  } catch (error) {
    console.warn('[IMU] 조회 실패:', error);
  } finally {
    fetching = false;
  }
}

export function startImuMonitor(deviceId) {
  render(null);

  if (
    !deviceId ||
    window.__imuPollStarted
  ) {
    return;
  }

  window.__imuPollStarted = true;

  poll(deviceId);

  setInterval(
    () => poll(deviceId),
    POLL_MS
  );
}