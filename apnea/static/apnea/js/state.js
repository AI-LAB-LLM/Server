// apnea/static/apnea/js/state.js
// 기존 ppg/static/js/state.js와 동일 (그대로 복사)

export const POPUP_ENABLED = true;

export let ITEMS = (typeof window !== 'undefined' && Array.isArray(window.ITEMS)) ? window.ITEMS : [];

export const charts = {
  rRatio: null,
  irHolding: null,
};

const MAX_ITEMS = 120;

export const RBUF_CAP = 900;
let RBUF = [];

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

export const IRBUF_CAP = 120;
let IRBUF = [];
let IRBUF_SEQ = 0;
let IRBUF_MAX_ID = Number.NEGATIVE_INFINITY;
let IRBUF_LAST_TS = null;
let IRBUF_LAST_SIG = null;

const toNum = v => {
  const n = Number(v);
  return Number.isFinite(n) ? n : null;
};

export function getIrbufPoints() { return IRBUF.slice(); }
export function resetIrbuf() {
  IRBUF = []; IRBUF_SEQ = 0;
  IRBUF_MAX_ID = Number.NEGATIVE_INFINITY;
  IRBUF_LAST_TS = null; IRBUF_LAST_SIG = null;
}

const ROLLBACK_ALLOW_MS = 10 * 60 * 1000;

export function appendIrFromItems(items) {
  const rows = Array.isArray(items) ? items : [];
  if (!rows.length) return;

  const enriched = rows.map(r => {
    const idNum = toNum(r.id ?? r.pk);
    const tsStr = r?.timestamp ?? r?.ts ?? null;
    const tsMs  = (tsStr ? new Date(tsStr).getTime() : null);
    return { r, idNum, tsMs: Number.isFinite(tsMs) ? tsMs : null, tsStr };
  });

  const byId = enriched
    .filter(o => o.idNum != null && o.idNum > IRBUF_MAX_ID)
    .sort((a, b) => a.idNum - b.idNum);

  const byTsRaw = enriched.filter(o => o.idNum == null && o.tsMs != null);
  const byTs = [];
  for (const o of byTsRaw) {
    if (IRBUF_LAST_TS == null || o.tsMs > IRBUF_LAST_TS) byTs.push(o);
    else if (IRBUF_LAST_TS - o.tsMs >= ROLLBACK_ALLOW_MS) {
      IRBUF_MAX_ID = Number.NEGATIVE_INFINITY;
      IRBUF_LAST_TS = null;
      IRBUF_LAST_SIG = null;
      byTs.push(o);
    }
  }

  const toAppend = [...byId, ...byTs];
  if (toAppend.length === 0 && enriched.length) {
    toAppend.push(enriched[enriched.length - 1]);
  }

  toAppend.sort((a, b) => {
    const ai = a.idNum ?? Number.NEGATIVE_INFINITY;
    const bi = b.idNum ?? Number.NEGATIVE_INFINITY;
    if (ai !== bi) return ai - bi;
    const at = a.tsMs ?? Number.NEGATIVE_INFINITY;
    const bt = b.tsMs ?? Number.NEGATIVE_INFINITY;
    return at - bt;
  });

  for (const { r, idNum, tsMs, tsStr } of toAppend) {
    const pred = r?.predictions?.APNEA_RESULT || {};
    const prob  = toNum(pred.prob);
    const valid = (pred.valid === true) || (pred.valid === 1) || (pred.valid === 'true');
    const thr   = toNum(pred.thr);
    const label = (pred.label === 0 || pred.label === 1) ? pred.label : null;

    const sig = JSON.stringify([tsStr, prob, valid ? 1 : 0, label]);
    if (IRBUF_LAST_SIG && IRBUF_LAST_SIG === sig) continue;

    IRBUF.push({ x: IRBUF_SEQ++, y: prob ?? null, valid, ts: tsStr ?? null, thr, label });
    if (IRBUF.length > IRBUF_CAP) IRBUF.splice(0, IRBUF.length - IRBUF_CAP);

    if (idNum != null && idNum > IRBUF_MAX_ID) IRBUF_MAX_ID = idNum;
    if (tsMs  != null && (IRBUF_LAST_TS == null || tsMs > IRBUF_LAST_TS)) IRBUF_LAST_TS = tsMs;
    IRBUF_LAST_SIG = sig;
  }
}