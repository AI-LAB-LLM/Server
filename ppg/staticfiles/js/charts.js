import { charts, safelyGet, latestItem, getRbufPoints, getIrbufPoints } from './state.js';

const WINDOW_SEC = 240;
const IR_THR_CONST = 0.53;
window.IR_MAX_CHUNKS ??= 120;
const MAX_CHUNKS = window.IR_MAX_CHUNKS;

// R ratio
export function renderRratio() {
  const cont = document.getElementById('ppgRratio');
  if (!cont || !window.CanvasJS) return;

  // 링버퍼에서 바로 읽기
  const pts = getRbufPoints();
  if (!pts || pts.length === 0) return;

  const maxX = pts.length - 1;
  const vMin = Math.max(0, maxX - WINDOW_SEC);
  const vMax = maxX;

  if (!charts.rRatio) {
    charts.rRatio = new CanvasJS.Chart('ppgRratio', {
      title: { text: 'R RATIO (AC/DC of Red) / (AC/DC of IR)', fontSize: 20, fontWeight: 'normal', fontFamily: 'Arial' },
      animationEnabled: false,
      zoomEnabled: false,
      interactivityEnabled: true,
      axisX: {
        title: 'Time (s)',
        interval: 12,
        labelFormatter: e => `${e.value - vMin}s`,
        viewportMinimum: vMin,
        viewportMaximum: vMax,
      },
      axisY: { title: 'R', minimum: 0, maximum: 2, interval: 0.25 },
      data: [{ type: 'line', markerSize: 0, dataPoints: [] }],
    });
  }

  // dataPoints 배열 재사용
  const dp = charts.rRatio.options.data[0].dataPoints;
  dp.length = 0;
  for (let i = 0; i < pts.length; i++) dp.push(pts[i]);

  charts.rRatio.options.axisX.viewportMinimum = vMin;
  charts.rRatio.options.axisX.viewportMaximum = vMax;
  charts.rRatio.options.axisX.labelFormatter = e => `${e.value - vMin}s`;
  charts.rRatio.render();

  const el = document.getElementById('ppgDecision');
  if (el) el.style.display = 'none';
}

// IR Holding
export function renderIrHolding() {
  const elChart = document.getElementById('irHoldingChart');
  const elMeta  = document.getElementById('irHoldingMeta');
  if (!elChart || !window.CanvasJS) return;

  const bufFull = getIrbufPoints();
  if (!Array.isArray(bufFull) || bufFull.length === 0) {
    if (elMeta) elMeta.textContent = 'No IR predictions yet.';
    return;
  }

  // 최근 20개만 표시
  const DISPLAY_COUNT = 20;
  const buf = bufFull.slice(-DISPLAY_COUNT);
  if (buf.length === 0) {
    if (elMeta) elMeta.textContent = 'No IR predictions yet.';
    return;
  }

  const lastX = buf[buf.length - 1].x ?? 0;
  const range = buf.length;
  const vMax  = lastX;
  const vMin  = Math.max(0, lastX - (range - 1));

  const ptsProb = buf.map(p => {
  const y = Number(p.y);
  return { x: p.x, y: Number.isFinite(y) ? y : null };
  });
  const thrBase = (typeof IR_THR_CONST === 'number' && Number.isFinite(IR_THR_CONST)) ? IR_THR_CONST : 0.53;

  const hotIdx = [];   // threshold 초과 지점
  const bands  = [];   // invalid 구간
  for (let i = 0; i < buf.length; i++) {
    const y = Number(buf[i].y);
    if (buf[i].valid !== true) bands.push(buf[i].x);
    if (Number.isFinite(y) && y > thrBase) hotIdx.push(buf[i].x);
  }

  const dataSeries = [
    { type: 'line', markerSize: 5, name: 'p(holding)', dataPoints: ptsProb }
  ];
  if (hotIdx.length) {
    dataSeries.push({
      type: 'column', axisYType: 'secondary', name: 'over-thr', showInLegend: false,
      dataPoints: hotIdx.map(x => ({ x, y: 1 })), color: 'rgba(239,68,68,0.25)', markerSize: 0, dataPointWidth: 14
    });
  }
  if (bands.length) {
    dataSeries.push({
      type: 'column', axisYType: 'secondary', name: 'invalid', showInLegend: false,
      dataPoints: bands.map(x => ({ x, y: 1 })), color: 'rgba(59,130,246,0.25)', markerSize: 0, dataPointWidth: 14
    });
  }

  const axisY = {
    title: 'probability', minimum: 0, maximum: 1, interval: 0.1,
    stripLines: [{ value: thrBase, thickness: 2, color: '#ef4444', label: `thr=${thrBase.toFixed(2)}` }]
  };
  const axisY2 = (hotIdx.length || bands.length)
    ? { minimum: 0, maximum: 1, gridThickness: 0, lineThickness: 0, tickLength: 0, labelFormatter: ()=>'' }
    : {};

  // 차트 생성/갱신
  if (!charts.irHolding) {
    charts.irHolding = new CanvasJS.Chart('irHoldingChart', {
      title: { text: 'Real-time Apnea Detection Results', fontSize: 20, fontWeight: 'normal', fontFamily: 'Arial' },
      animationEnabled: false,
      zoomEnabled: false,
      axisX: {
        title: 'chunk index',
        interval: 1,
        viewportMinimum: vMin,
        viewportMaximum: vMax,
        // 상대 인덱스로 0..19처럼 보이게
        labelFormatter: e => `${e.value - vMin}`
      },
      axisY,
      axisY2,
      data: dataSeries,
      toolTip: {
        shared: true,
        content: function(e) {
          const x  = e.entries?.[0]?.dataPoint?.x;
          const pt = buf.find(b => b.x === x);
          const p  = (pt?.y  != null) ? Number(pt.y).toFixed(3) : '-';
          const t  = thrBase.toFixed(2);
          const v  = (pt?.valid === true) ? 'valid' : 'invalid';
          const lb = (pt?.label != null) ? Number(pt.label) : '-';
          return [
            `<b>chunk ${x}</b>  <span style="color:#9aa0a6">${pt?.ts ?? '-'}</span>`,
            `prob=${p} / thr=${t} / label=${lb} / ${v}`
          ].join('<br/>');
        }
      },
      legend: { verticalAlign: 'bottom' }
    });
  } else {
    charts.irHolding.options.axisY  = axisY;
    charts.irHolding.options.axisY2 = axisY2;
    charts.irHolding.options.data   = dataSeries;
    charts.irHolding.options.axisX.viewportMinimum = vMin;
    charts.irHolding.options.axisX.viewportMaximum = vMax;
    charts.irHolding.options.axisX.labelFormatter  = e => `${e.value - vMin}`;
  }

  if (elChart.offsetWidth === 0 || elChart.offsetHeight === 0) return;

  charts.irHolding.render();
}

// Wear Status
export function renderWearStatus() {
  const card  = document.getElementById('wearCard');
  const elTxt = document.getElementById('wearStatusText');
  const elMeta= document.getElementById('wearStatusMeta');
  const elImg = document.getElementById('wearStateImage');
  if (!card || !elTxt || !elMeta || !elImg) return;

  const it = latestItem();
  const wear = it ? safelyGet(it, 'predictions.WEAR_GREEN', null) : null;
  const ts = it?.timestamp || '-';

  // 판단불가
  if (!wear || !wear.valid) {
    card.className = 'wear-card is-unk';
    elTxt.textContent  = 'Still checking...';
    elMeta.textContent = `invalid / ${ts}`;
    elImg.src = '/static/image/loading.png';
    return;
  }

  // 착용 상태
  if (wear.label === 1) {
    card.className = 'wear-card is-wear';
    elTxt.textContent  = 'Wearing';
    elMeta.textContent = `valid / ${ts}`;
    elImg.src = '/static/image/wear_on.png';
  }
  // 미착용 상태
  else {
    card.className = 'wear-card is-off';
    elTxt.textContent  = 'Not Wearing';
    elMeta.textContent = `valid / ${ts}`;
    elImg.src = '/static/image/wear_off.png';
  }
}