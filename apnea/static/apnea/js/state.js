// apnea/static/apnea/js/state.js

export const POPUP_ENABLED = true;

export let ITEMS = (typeof window !== 'undefined' && Array.isArray(window.ITEMS)) ? window.ITEMS : [];

export const charts = {
  rRatio: null,
  irHolding: null,
};

const MAX_ITEMS = 120;

export const RBUF_CAP = 900;
let RBUF = [];

export const IRBUF_CAP = 900;
let IRBUF = [];
let IRBUF_SEQ = 0;
let IRBUF_MAX_ID = Number.NEGATIVE_INFINITY;
let IRBUF_LAST_TS = null;
let IRBUF_LAST_SIG = null;

const toNum = v => {
  const n = Number(v);
  return Number.isFinite(n) ? n : null;
};

export function setItems(newItems) {
  const arr = Array.isArray(newItems) ? newItems : [];
  ITEMS = (arr.length > MAX_ITEMS) ? arr.slice(-MAX_ITEMS) : arr;
}

export function safelyGet(obj, path, def = null) {
  if (!obj || !path) return def;
  try {
    return path.split('.').reduce((acc, k) => (acc != null ? acc[k] : undefined), obj) ?? def;
  } catch {
    return def;
  }
}

export function latestItem() {
  return (Array.isArray(ITEMS) && ITEMS.length) ? ITEMS[ITEMS.length - 1] : null;
}

export function getRbufPoints() {
  return RBUF;
}

export function appendRFromItems(items) {
  const rows = Array.isArray(items) ? items : [];
  const sorted = [...rows].sort((a, b) => {
    const ta = new Date(a?.timestamp || 0).getTime();
    const tb = new Date(b?.timestamp || 0).getTime();
    return ta - tb;
  });

  const vals = [];
  for (const it of sorted) {
    const series = safelyGet(it, 'predictions.R_RATIO_SERIES.values', null);
    if (!Array.isArray(series)) continue;
    for (const r of series) {
      const y = (r != null && Number.isFinite(Number(r))) ? Number(r) : null;
      vals.push(y);
    }
  }

  const combined = vals.map((y, i) => ({ x: i, y }));
  const keep = (combined.length > RBUF_CAP) ? combined.slice(-RBUF_CAP) : combined;
  RBUF.length = 0;
  for (let i = 0; i < keep.length; i++) RBUF.push(keep[i]);
}

export function getIrbufPoints() { return IRBUF.slice(); }

export function resetIrbuf() {
  IRBUF = [];
  IRBUF_SEQ = 0;
  IRBUF_MAX_ID = Number.NEGATIVE_INFINITY;
  IRBUF_LAST_TS = null;
  IRBUF_LAST_SIG = null;
}

export function appendIrFromItems(items) {
  const rows = Array.isArray(items) ? items : [];
  if (!rows.length) return;

  const newRows = [...rows]
    .map(r => ({ r, idNum: toNum(r.id ?? r.pk) }))
    .filter(o => o.idNum != null && o.idNum > IRBUF_MAX_ID)
    .sort((a, b) => a.idNum - b.idNum);

  for (const { r, idNum } of newRows) {
    const beatResults = r?.beat_results;
    const pred = r?.predictions?.APNEA_RESULT;

    if (Array.isArray(beatResults) && beatResults.length > 0 && pred) {
      for (const beat of beatResults) {
        const timeSec = beat?.time_sec;
        const prob    = beat?.p_apnea_smooth;
        const valid   = beat?.status === 'ok';
        const label   = beat?.pred_label;

        if (timeSec == null) continue;

        const x = Number(timeSec);
        if (!Number.isFinite(x)) continue;

        // time_sec가 이전보다 작아지면 새 세션으로 판단하고 기존 그래프 제거
        if (IRBUF.length > 0 && x < IRBUF[IRBUF.length - 1].x) {
          IRBUF.length = 0;
        }

        IRBUF.push({
          x,
          y: (prob != null && Number.isFinite(Number(prob))) ? Number(prob) : null,
          valid,
          ts: r?.timestamp ?? null,
          thr: null,
          label,
        });
      }
    }

    if (IRBUF.length > IRBUF_CAP) {
      IRBUF.splice(0, IRBUF.length - IRBUF_CAP);
    }

    if (idNum > IRBUF_MAX_ID) {
      IRBUF_MAX_ID = idNum;
    }
  }
}