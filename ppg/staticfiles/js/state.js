// pop up on/off
export const POPUP_ENABLED = true;

export let ITEMS = (typeof window !== 'undefined' && Array.isArray(window.ITEMS)) ? window.ITEMS : [];

// 차트 인스턴스 캐시
export const charts = {
  rRatio: null,
  irHolding: null,
};

// 메모리 상한
const MAX_ITEMS = 120;

export const RBUF_CAP = 900; //1Hz로 15분=900
let RBUF = []; // [{x, y}] 형태로만 저장

// 최근 아이템 교체
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

// 링버퍼 포인터 조회
export function getRbufPoints() {
  return RBUF;
}

// 링버퍼 갱신
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

  // 상한 적용: 뒤에서 RBUF_CAP개만 유지
  const keep = (combined.length > RBUF_CAP) ? combined.slice(-RBUF_CAP) : combined;

  RBUF.length = 0;
  for (let i = 0; i < keep.length; i++) RBUF.push(keep[i]);
}

export const IRBUF_CAP = window.IR_MAX_CHUNKS || 120;
let IRBUF = [];                 // { x, y, valid, ts, thr, label }
let IRBUF_SEQ = 0;
let IRBUF_MAX_ID = Number.NEGATIVE_INFINITY;
let IRBUF_LAST_TS = null;
let IRBUF_LAST_SIG = null;

const toNum = v => {
  const n = Number(v);
  return Number.isFinite(n) ? n : null;
};

export function getIrbufPoints(){ return IRBUF.slice(); }
export function resetIrbuf(){
  IRBUF = []; IRBUF_SEQ = 0;
  IRBUF_MAX_ID = Number.NEGATIVE_INFINITY;
  IRBUF_LAST_TS = null; IRBUF_LAST_SIG = null;
}

const ROLLBACK_ALLOW_MS = 10 * 60 * 1000;

export function appendIrFromItems(items){
  const rows = Array.isArray(items) ? items : [];
  if (!rows.length) return;

  const enriched = rows.map(r=>{
    const idNum = toNum(r.id ?? r.pk);
    const tsStr = r?.timestamp ?? r?.ts ?? null;
    const tsMs  = (tsStr ? new Date(tsStr).getTime() : null);
    return { r, idNum, tsMs: Number.isFinite(tsMs)?tsMs:null, tsStr };
  });

  // id가 있는 것: 본 최대 id 초과만
  const byId = enriched
    .filter(o => o.idNum != null && o.idNum > IRBUF_MAX_ID)
    .sort((a,b)=>a.idNum-b.idNum);

  // id가 없는 것: timestamp 보조
  const byTsRaw = enriched.filter(o => o.idNum == null && o.tsMs != null);
  const byTs = [];
  for (const o of byTsRaw){
    if (IRBUF_LAST_TS == null || o.tsMs > IRBUF_LAST_TS) byTs.push(o);
    else if (IRBUF_LAST_TS - o.tsMs >= ROLLBACK_ALLOW_MS){
      // 큰 폭의 과거 → 새 세션으로 간주
      IRBUF_MAX_ID = Number.NEGATIVE_INFINITY;
      IRBUF_LAST_TS = null;
      IRBUF_LAST_SIG = null;
      byTs.push(o);
    }
  }

  // 둘 다 비면 → 마지막 아이템 1개는 강제 반영
  const toAppend = [...byId, ...byTs];
  if (toAppend.length === 0 && enriched.length){
    const o = enriched[enriched.length-1]; // 최신으로 간주
    toAppend.push(o);
  }

  // append 순서 안정화
  toAppend.sort((a,b)=>{
    const ai = a.idNum ?? Number.NEGATIVE_INFINITY;
    const bi = b.idNum ?? Number.NEGATIVE_INFINITY;
    if (ai !== bi) return ai - bi;
    const at = a.tsMs ?? Number.NEGATIVE_INFINITY;
    const bt = b.tsMs ?? Number.NEGATIVE_INFINITY;
    return at - bt;
  });

  // 실제 적재
  for (const { r, idNum, tsMs, tsStr } of toAppend){
    const p  = r?.predictions || r?.PREDICTIONS || {};
    const ir = p.IR || p.IR_HOLDING || p.ir || p.IR_PRED || {};
    const prob  = toNum(ir.prob ?? ir.p ?? p.IR_PROB ?? r.IR_PROB);
    const valid = (ir.valid === true) || (ir.valid === 1) || (ir.valid === 'true');
    const thr   = toNum(ir.thr);
    const label = (ir.label === 0 || ir.label === 1) ? ir.label : null;

    // 동일 값 중복 방지 서명 (ts,prob,valid,label 조합)
    const sig = JSON.stringify([tsStr, prob, valid?1:0, label]);
    if (IRBUF_LAST_SIG && IRBUF_LAST_SIG === sig) {
      // 완전 동일하면 스킵 (중복 채움 방지)
      continue;
    }

    IRBUF.push({ x: IRBUF_SEQ++, y: prob ?? null, valid, ts: tsStr ?? null, thr, label });
    if (IRBUF.length > IRBUF_CAP) IRBUF.splice(0, IRBUF.length - IRBUF_CAP);

    if (idNum != null && idNum > IRBUF_MAX_ID) IRBUF_MAX_ID = idNum;
    if (tsMs  != null && (IRBUF_LAST_TS == null || tsMs > IRBUF_LAST_TS)) IRBUF_LAST_TS = tsMs;
    IRBUF_LAST_SIG = sig; // 마지막 서명 갱신
  }
}