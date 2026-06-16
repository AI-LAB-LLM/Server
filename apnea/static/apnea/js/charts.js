// apnea/static/apnea/js/charts.js
// 기존 ppg/static/js/charts.js와 동일. 타이틀만 Apnea 전용으로 변경.

import { charts, safelyGet, latestItem, getRbufPoints, getIrbufPoints } from './state.js';

const WINDOW_SEC = 240;
const APNEA_THR_CONST = 0.5;  // 기본 임계값 (모델 config에서 덮어씀)
window.IR_MAX_CHUNKS ??= 120;


export function renderRratio() {
  const cont = document.getElementById('ppgRratio');
  if (!cont || !window.CanvasJS) return;

  const pts = getRbufPoints();
  if (!pts || pts.length === 0) return;

  const maxX = pts[pts.length - 1].x;
  const minX = pts[0].x;
  const vMin = Math.max(minX, maxX - WINDOW_SEC);
  const vMax = maxX;

  if (charts.rRatio) {
    // 기존 차트 데이터만 업데이트
    const dp = charts.rRatio.options.data[0].dataPoints;
    dp.length = 0;
    for (let i = 0; i < pts.length; i++) dp.push(pts[i]);
    charts.rRatio.options.axisX.viewportMinimum = vMin;
    charts.rRatio.options.axisX.viewportMaximum = vMax;
    charts.rRatio.options.axisX.labelFormatter = e => `${Math.round(e.value - vMin)}s`;
    charts.rRatio.render();
    return;
  }

  // 최초 생성
  charts.rRatio = new CanvasJS.Chart('ppgRratio', {
    title: { text: 'R ratio (Red/IR)', fontSize: 18, fontWeight: 'normal', fontFamily: 'Arial' },
    animationEnabled: false,
    zoomEnabled: false,
    interactivityEnabled: true,
    axisX: {
      title: 'Time (s)',
      interval: 12,
      labelFormatter: e => `${Math.round(e.value - vMin)}s`,
      viewportMinimum: vMin,
      viewportMaximum: vMax,
    },
    axisY: { title: 'R ratio (Red/IR)', minimum: 0, maximum: 4, interval: 0.5 },
    data: [{ type: 'line', markerSize: 0, dataPoints: pts.slice() }],
  });
  charts.rRatio.render();
}

export function renderIrHolding() {
  const elChart = document.getElementById('irHoldingChart');
  const elMeta  = document.getElementById('irHoldingMeta');
  if (!elChart || !window.CanvasJS) return;

  const bufFull = getIrbufPoints();
  if (!Array.isArray(bufFull) || bufFull.length === 0) {
    if (elMeta) elMeta.textContent = 'No apnea predictions yet. Waiting for baseline...';
    return;
  }

  // x축 0~600 고정
// x축: 최신 time_sec 기준으로 600초 슬라이딩
  const WINDOW = 600;
  const lastX  = bufFull.length > 0 ? bufFull[bufFull.length - 1].x : WINDOW;
  const X_MAX  = lastX;
  const X_MIN  = Math.max(0, lastX - WINDOW);

  const ptsProb = bufFull.map(p => {
    const y = Number(p.y);
    return { x: p.x, y: Number.isFinite(y) ? y : null };
  });

  const thrBase = (window.__apneaThr && Number.isFinite(window.__apneaThr))
    ? window.__apneaThr
    : 0.5;

  const hotIdx = [];
  const bands  = [];
  for (const pt of bufFull) {
    const y = Number(pt.y);
    if (pt.valid !== true || pt.wear_valid !== true ) {bands.push(pt.x);
    }else if (Number.isFinite(y) && y > thrBase) {hotIdx.push(pt.x);}
  }

  const dataSeries = [
    { type: 'line', markerSize: 3, name: 'p(apnea)', dataPoints: ptsProb, connectNullData: false }
  ];
  if (hotIdx.length) {
    dataSeries.push({
      type: 'column', axisYType: 'secondary', name: 'Apnea',
      showInLegend: true,
      dataPoints: hotIdx.map(x => ({ x, y: 1 })),
      color: 'rgba(239,68,68,0.25)', markerSize: 0, dataPointWidth: 3
    });
  }
  if (bands.length) {
    dataSeries.push({
      type: 'column', axisYType: 'secondary', name: 'invalid',
      showInLegend: true,
      dataPoints: bands.map(x => ({ x, y: 1 })),
      color: 'rgba(59,130,246,0.25)', markerSize: 0, dataPointWidth: 3
    });
  }

  const axisY = {
    title: 'probability', minimum: 0, maximum: 1, interval: 0.1,
    stripLines: [{ value: thrBase, thickness: 2, color: '#ef4444',
                   label: `thr=${thrBase.toFixed(2)}` }]
  };
  const axisY2 = (hotIdx.length || bands.length)
    ? { minimum: 0, maximum: 1, gridThickness: 0, lineThickness: 0,
        tickLength: 0, labelFormatter: () => '' }
    : {};

  if (!charts.irHolding) {
    charts.irHolding = new CanvasJS.Chart('irHoldingChart', {
      title: { text: 'Real-time Apnea Detection (Smoothed Probability)',
               fontSize: 18, fontWeight: 'normal', fontFamily: 'Arial' },
      animationEnabled: false,
      zoomEnabled: false,
      axisX: {
        title: 'time (sec)',
        minimum: X_MIN,
        maximum: X_MAX,
        interval: 60,
        viewportMinimum: X_MIN,
        viewportMaximum: X_MAX,
      },
      axisY,
      axisY2,
      data: dataSeries,
      toolTip: {
        shared: true,
        content: function(e) {
          const x  = e.entries?.[0]?.dataPoint?.x;
          const pt = bufFull.find(b => Math.abs(b.x - x) < 0.01);         
          const p  = (pt?.y != null) ? Number(pt.y).toFixed(3) : '-';
          const t  = thrBase.toFixed(2);
          const v  = (pt?.valid === true) ? 'ok' : 'baseline/warming-up';
          const lb = (pt?.label != null) ? Number(pt.label) : '-';
          return `<b>${x?.toFixed(1)}s</b><br>p(apnea)=${p} / thr=${t} / label=${lb} / ${v}`;
        }
      },
      legend: { verticalAlign: 'bottom' }
    });
  } else {
    charts.irHolding.options.axisY  = axisY;
    charts.irHolding.options.axisY2 = axisY2;
    charts.irHolding.options.data   = dataSeries;
    charts.irHolding.options.axisX.minimum         = X_MIN;
    charts.irHolding.options.axisX.maximum         = X_MAX;
    charts.irHolding.options.axisX.viewportMinimum = X_MIN;
    charts.irHolding.options.axisX.viewportMaximum = X_MAX;
  }

  if (elChart.offsetWidth === 0 || elChart.offsetHeight === 0) return;
  charts.irHolding.render();

  if (elMeta) {
    const last = bufFull[bufFull.length - 1];
    const status = last?.valid ? 'inference' : 'baseline/warming-up';
    elMeta.textContent = `Latest: p=${last?.y != null ? Number(last.y).toFixed(3) : '-'} | ${status}`;
  }
}

export function renderWearStatus() {
  const card  = document.getElementById('wearCard');
  const elTxt = document.getElementById('wearStatusText');
  const elMeta= document.getElementById('wearStatusMeta');
  const elImg = document.getElementById('wearStateImage');
  if (!card || !elTxt || !elMeta || !elImg) return;

  const it = latestItem();
  const wear = it ? safelyGet(it, 'predictions.WEAR_GREEN', null) : null;
  const ts = it?.timestamp || '-';

  if (!wear || !wear.valid) {
    card.className = 'card wear-card is-unk';
    elTxt.textContent  = 'Still checking...';
    elMeta.textContent = `invalid / ${ts}`;
    elImg.src = '/static/apnea/image/loading.png';
    return;
  }

  if (wear.label === 1) {
    card.className = 'card wear-card is-wear';
    elTxt.textContent  = 'Wearing';
    elMeta.textContent = `valid / ${ts}`;
    elImg.src = '/static/apnea/image/wear_on.png';
  } else {
    card.className = 'card wear-card is-off';
    elTxt.textContent  = 'Not Wearing';
    elMeta.textContent = `valid / ${ts}`;
    elImg.src = '/static/apnea/image/wear_off.png';
  }
}