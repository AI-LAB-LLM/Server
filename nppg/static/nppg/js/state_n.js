/**
 * state_n.js
 * 기존 ppg state.js와 완전히 동일합니다.
 * IR_HOLDING 키를 그대로 읽으므로 변경이 없습니다.
 */

export const POPUP_ENABLED = true;
export let ITEMS = Array.isArray(window.ITEMS) ? window.ITEMS : [];
export const charts = { rRatio: null, irHolding: null };

const MAX_ITEMS  = 120;
export const RBUF_CAP  = 900;
export const IRBUF_CAP = window.IR_MAX_CHUNKS || 120;

let RBUF = [];
let IRBUF = [];
let IRBUF_SEQ    = 0;
let IRBUF_MAX_ID = Number.NEGATIVE_INFINITY;
let IRBUF_LAST_TS  = null;
let IRBUF_LAST_SIG = null;
const ROLLBACK_ALLOW_MS = 10 * 60 * 1000;

const toNum = v => { const n = Number(v); return Number.isFinite(n) ? n : null; };

export function setItems(newItems) {
  const arr = Array.isArray(newItems) ? newItems : [];
  ITEMS = arr.length > MAX_ITEMS ? arr.slice(-MAX_ITEMS) : arr;
}

export function safelyGet(obj, path, def = null) {
  if (!obj || !path) return def;
  try {
    return path.split('.').reduce((acc, k) => acc != null ? acc[k] : undefined, obj) ?? def;
  } catch { return def; }
}

export function latestItem() {
  return Array.isArray(ITEMS) && ITEMS.length ? ITEMS[ITEMS.length - 1] : null;
}

export function getRbufPoints()  { return RBUF; }
export function getIrbufPoints() { return IRBUF.slice(); }

export function resetIrbuf() {
  IRBUF = []; IRBUF_SEQ = 0;
  IRBUF_MAX_ID = Number.NEGATIVE_INFINITY;
  IRBUF_LAST_TS = null; IRBUF_LAST_SIG = null;
}

/** R Ratio 링버퍼 갱신 — 기존 state.js와 동일 */
export function appendRFromItems(items) {
  const sorted = [...(Array.isArray(items) ? items : [])].sort(
    (a, b) => new Date(a?.timestamp || 0) - new Date(b?.timestamp || 0)
  );
  const vals = [];
  for (const it of sorted) {
    const series = safelyGet(it, 'predictions.R_RATIO_SERIES.values', null);
    if (!Array.isArray(series)) continue;
    for (const r of series) vals.push(Number.isFinite(Number(r)) ? Number(r) : null);
  }
  const combined = vals.map((y, i) => ({ x: i, y }));
  const keep = combined.length > RBUF_CAP ? combined.slice(-RBUF_CAP) : combined;
  RBUF.length = 0;
  keep.forEach(p => RBUF.push(p));
}

/**
 * 무호흡 감지 링버퍼 갱신.
 * IR_HOLDING 키를 그대로 읽습니다.
 * 새 모델 결과(prob, smooth_prob, label)도 동일한 키 구조입니다.
 */
export function appendIrFromItems(items) {
  const rows = Array.isArray(items) ? items : [];
  if (!rows.length) return;

  const enriched = rows.map(r => {
    const idNum = toNum(r.id ?? r.pk);
    const tsStr = r?.timestamp ?? null;
    const tsMs  = tsStr ? new Date(tsStr).getTime() : null;
    return { r, idNum, tsMs: Number.isFinite(tsMs) ? tsMs : null, tsStr };
  });

  const byId = enriched
    .filter(o => o.idNum != null && o.idNum > IRBUF_MAX_ID)
    .sort((a, b) => a.idNum - b.idNum);

  const byTs = [];
  for (const o of enriched.filter(o => o.idNum == null && o.tsMs != null)) {
    if (IRBUF_LAST_TS == null || o.tsMs > IRBUF_LAST_TS) {
      byTs.push(o);
    } else if (IRBUF_LAST_TS - o.tsMs >= ROLLBACK_ALLOW_MS) {
      IRBUF_MAX_ID = Number.NEGATIVE_INFINITY;
      IRBUF_LAST_TS = null; IRBUF_LAST_SIG = null;
      byTs.push(o);
    }
  }

  const toAppend = [...byId, ...byTs];
  if (!toAppend.length && enriched.length) toAppend.push(enriched[enriched.length - 1]);

  toAppend.sort((a, b) => {
    const ai = a.idNum ?? Number.NEGATIVE_INFINITY;
    const bi = b.idNum ?? Number.NEGATIVE_INFINITY;
    return ai !== bi ? ai - bi : (a.tsMs ?? 0) - (b.tsMs ?? 0);
  });

  for (const { r, idNum, tsMs, tsStr } of toAppend) {
    const p   = r?.predictions || {};
    const ir  = p.IR_HOLDING || {};
    // smooth_prob 우선, 없으면 prob 사용 (새 모델 결과)
    const prob  = toNum(ir.smooth_prob ?? ir.prob);
    const valid = ir.valid === true || ir.valid === 1;
    const label = (ir.label === 0 || ir.label === 1) ? ir.label : null;
    const thr   = toNum(ir.thr ?? null);

    const sig = JSON.stringify([tsStr, prob, valid ? 1 : 0, label]);
    if (IRBUF_LAST_SIG === sig) continue;

    IRBUF.push({ x: IRBUF_SEQ++, y: prob ?? null, valid, ts: tsStr ?? null, thr, label });
    if (IRBUF.length > IRBUF_CAP) IRBUF.splice(0, IRBUF.length - IRBUF_CAP);

    if (idNum != null && idNum > IRBUF_MAX_ID) IRBUF_MAX_ID = idNum;
    if (tsMs  != null && (IRBUF_LAST_TS == null || tsMs > IRBUF_LAST_TS)) IRBUF_LAST_TS = tsMs;
    IRBUF_LAST_SIG = sig;
  }
}