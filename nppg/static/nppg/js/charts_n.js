/**
 * charts_n.js
 * 기존 ppg charts.js와 동일합니다.
 * IR_HOLDING 구조가 동일하므로 차트 코드는 변경이 없습니다.
 * threshold 기본값만 0.5로 설정합니다 (새 모델 기준).
 */

import { charts, safelyGet, latestItem, getRbufPoints, getIrbufPoints } from './state_n.js';

const WINDOW_SEC = 240;
window.IR_MAX_CHUNKS ??= 120;

export function renderRratio() {
  const cont = document.getElementById('ppgRratio');
  if (!cont || !window.CanvasJS) return;
  const pts = getRbufPoints();
  if (!pts?.length) return;

  const maxX = pts.length - 1;
  const vMin = Math.max(0, maxX - WINDOW_SEC);
  const vMax = maxX;

  if (!charts.rRatio) {
    charts.rRatio = new CanvasJS.Chart('ppgRratio', {
      title: { text: 'R RATIO (AC/DC of Red) / (AC/DC of IR)', fontSize: 20,
               fontWeight: 'normal', fontFamily: 'Arial' },
      animationEnabled: false,
      axisX: { title: 'Time (s)', interval: 12,
               labelFormatter: e => `${e.value - vMin}s`,
               viewportMinimum: vMin, viewportMaximum: vMax },
      axisY: { title: 'R', minimum: 0, maximum: 2, interval: 0.25 },
      data:  [{ type: 'line', markerSize: 0, dataPoints: [] }],
    });
  }

  const dp = charts.rRatio.options.data[0].dataPoints;
  dp.length = 0;
  pts.forEach(p => dp.push(p));
  charts.rRatio.options.axisX.viewportMinimum = vMin;
  charts.rRatio.options.axisX.viewportMaximum = vMax;
  charts.rRatio.options.axisX.labelFormatter  = e => `${e.value - vMin}s`;
  charts.rRatio.render();

  const el = document.getElementById('ppgDecision');
  if (el) el.style.display = 'none';
}

export function renderIrHolding() {
  const elChart = document.getElementById('irHoldingChart');
  const elMeta  = document.getElementById('irHoldingMeta');
  if (!elChart || !window.CanvasJS) return;

  const bufFull = getIrbufPoints();
  if (!bufFull.length) {
    if (elMeta) elMeta.textContent = 'No predictions yet.';
    return;
  }

  const buf   = bufFull.slice(-20);
  const lastX = buf[buf.length - 1].x ?? 0;
  const vMin  = Math.max(0, lastX - (buf.length - 1));
  const vMax  = lastX;

  // thr는 새 모델 결과에 포함된 값 사용, 없으면 0.5 기본값
  const thrBase = buf[buf.length - 1]?.thr ?? 0.5;

  const ptsProb = buf.map(p => ({
    x: p.x,
    y: Number.isFinite(Number(p.y)) ? Number(p.y) : null,
  }));

  const hotIdx = buf.filter(p => Number.isFinite(Number(p.y)) && Number(p.y) > thrBase).map(p => p.x);
  const bands  = buf.filter(p => p.valid !== true).map(p => p.x);

  const dataSeries = [{ type: 'line', markerSize: 5, name: 'p(apnea)', dataPoints: ptsProb }];
  if (hotIdx.length)
    dataSeries.push({
      type: 'column', axisYType: 'secondary', name: 'over-thr',
      dataPoints: hotIdx.map(x => ({ x, y: 1 })),
      color: 'rgba(239,68,68,0.25)', markerSize: 0, dataPointWidth: 14,
    });
  if (bands.length)
    dataSeries.push({
      type: 'column', axisYType: 'secondary', name: 'invalid',
      dataPoints: bands.map(x => ({ x, y: 1 })),
      color: 'rgba(59,130,246,0.25)', markerSize: 0, dataPointWidth: 14,
    });

  const axisY = {
    title: 'probability', minimum: 0, maximum: 1, interval: 0.1,
    stripLines: [{ value: thrBase, thickness: 2, color: '#ef4444',
                   label: `thr=${thrBase.toFixed(2)}` }],
  };
  const axisY2 = (hotIdx.length || bands.length)
    ? { minimum: 0, maximum: 1, gridThickness: 0, lineThickness: 0,
        tickLength: 0, labelFormatter: () => '' }
    : {};

  if (!charts.irHolding) {
    charts.irHolding = new CanvasJS.Chart('irHoldingChart', {
      title: { text: 'Real-time Apnea Detection (NPPG)', fontSize: 20,
               fontWeight: 'normal', fontFamily: 'Arial' },
      animationEnabled: false,
      axisX: { title: 'chunk index', interval: 1,
               viewportMinimum: vMin, viewportMaximum: vMax,
               labelFormatter: e => `${e.value - vMin}` },
      axisY, axisY2, data: dataSeries,
      toolTip: {
        shared: true,
        content(e) {
          const x  = e.entries?.[0]?.dataPoint?.x;
          const pt = buf.find(b => b.x === x);
          const p  = pt?.y != null ? Number(pt.y).toFixed(3) : '-';
          const v  = pt?.valid === true ? 'valid' : 'invalid';
          const lb = pt?.label != null ? Number(pt.label) : '-';
          return `<b>chunk ${x}</b><br/>prob=${p} / label=${lb} / ${v}`;
        },
      },
    });
  } else {
    charts.irHolding.options.axisY  = axisY;
    charts.irHolding.options.axisY2 = axisY2;
    charts.irHolding.options.data   = dataSeries;
    charts.irHolding.options.axisX.viewportMinimum = vMin;
    charts.irHolding.options.axisX.viewportMaximum = vMax;
    charts.irHolding.options.axisX.labelFormatter  = e => `${e.value - vMin}`;
  }

  if (elChart.offsetWidth && elChart.offsetHeight) charts.irHolding.render();
}

/** 착용 상태 렌더링 — 기존 ppg charts.js의 renderWearStatus와 동일 */
export function renderWearStatus() {
  const card   = document.getElementById('wearCard');
  const elTxt  = document.getElementById('wearStatusText');
  const elMeta = document.getElementById('wearStatusMeta');
  const elImg  = document.getElementById('wearStateImage');
  if (!card || !elTxt || !elMeta || !elImg) return;

  const it   = latestItem();
  const wear = it ? safelyGet(it, 'predictions.WEAR_GREEN', null) : null;
  const ts   = it?.timestamp || '-';

  if (!wear || !wear.valid) {
    card.className     = 'wear-card is-unk';
    elTxt.textContent  = 'Still checking...';
    elMeta.textContent = `invalid / ${ts}`;
    elImg.src          = '/static/nppg/image/loading.png';
    return;
  }
  if (wear.label === 1) {
    card.className     = 'wear-card is-wear';
    elTxt.textContent  = 'Wearing';
    elMeta.textContent = `valid / ${ts}`;
    elImg.src          = '/static/nppg/image/wear_on.png';
  } else {
    card.className     = 'wear-card is-off';
    elTxt.textContent  = 'Not Wearing';
    elMeta.textContent = `valid / ${ts}`;
    elImg.src          = '/static/nppg/image/wear_off.png';
  }
}