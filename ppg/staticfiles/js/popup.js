import { POPUP_ENABLED } from './state.js';

export function initWearPopup() {
  if (!POPUP_ENABLED) {
    document.getElementById('wearPopup')?.classList.add('wear-popup--hidden');
    document.getElementById('wearOverlay')?.classList.remove('wear-overlay--visible');
    document.body.classList.remove('modal-open');
    document.getElementById('appRoot')?.classList.remove('blurred');
    return { onNewRecord() {}, openStartSession() {} };
  }

  const el = {
    root: document.getElementById('wearPopup'),
    overlay: document.getElementById('wearOverlay'),
    status: document.getElementById('wp-status'),
    text: document.getElementById('wp-text'),
    bar: document.getElementById('wp-progressbar'),
    sec: document.getElementById('wp-seconds'),
    close: document.getElementById('wp-close'),
  };

  const state = {
    sessionActive: false,
    startedAt: null,
    tick: null,
    forceHidden: false,
  };

  const LIMIT = 84; // 진행바 최대(초)

  const show = () => {
    if (!el.root) return;
    el.root.classList.remove('wear-popup--hidden');
    el.root.removeAttribute('aria-hidden');
    el.root.removeAttribute('inert');
    el.overlay?.classList.add('wear-overlay--visible');
    document.body.classList.add('modal-open');
    document.getElementById('appRoot')?.classList.add('blurred');
    (el.close || el.root).focus?.();
  };

  const hide = () => {
    if (!el.root) return;
    el.close?.blur?.();
    (document.getElementById('btnStartNew') || document.body).focus?.();
    el.root.classList.add('wear-popup--hidden');
    el.root.setAttribute('aria-hidden', 'true');
    el.root.setAttribute('inert', '');
    el.overlay?.classList.remove('wear-overlay--visible');
    document.body.classList.remove('modal-open');
    document.getElementById('appRoot')?.classList.remove('blurred');
  };

  const setStatus = (txt) => {
    if (!el.status) return;
    el.status.textContent = txt;
    el.root?.classList.remove('wear-popup--warning');
  };

  const reset = () => {
    clearInterval(state.tick);
    state.tick = null;
    state.startedAt = null;
    if (el.sec) el.sec.textContent = '0';
    if (el.bar) el.bar.style.width = '0%';
  };

  const start = () => {
    if (state.tick) return;
    state.tick = setInterval(() => {
      if (!state.startedAt) return;
      const elapsed = Math.floor((Date.now() - state.startedAt) / 1000);
      const sec = Math.min(elapsed, LIMIT);
      if (el.sec) el.sec.textContent = String(sec);
      if (el.bar) el.bar.style.width = `${(sec / LIMIT) * 100}%`;

      if (sec >= LIMIT && !state.forceHidden) {
        hide();
        clearInterval(state.tick);
        state.tick = null;
        state.sessionActive = false;
      }
    }, 200);
  };

  // 닫기 버튼
  el.close?.addEventListener('click', () => {
    state.forceHidden = true;
    hide();
    clearInterval(state.tick);
    state.tick = null;
    state.sessionActive = false;
  });

  function onNewRecord(_item) {
    if (!state.sessionActive) return;
  }

  function openStartSession(startedAtMs) {
    state.sessionActive = true;
    state.forceHidden = false;

    state.startedAt = Number.isFinite(startedAtMs) ? startedAtMs : Date.now();
    const d = new Date(state.startedAt);

    setStatus('대기 중');
    if (el.text) {
      el.text.innerHTML = `데이터를 측정 중입니다...<br/>시작 시각: <b>${d.toLocaleString()}</b>`;
    }

    if (el.sec) el.sec.textContent = '0';
    if (el.bar) el.bar.style.width = '0%';
    if (state.tick) { clearInterval(state.tick); state.tick = null; }

    show();
    start();

    const last = Array.isArray(window.ITEMS) ? window.ITEMS.at(-1) : null;
    if (last) onNewRecord(last);
  }

  if (el.root) {
    const hidden = el.root.classList.contains('wear-popup--hidden');
    if (hidden) { el.root.setAttribute('aria-hidden','true'); el.root.setAttribute('inert',''); }
    else { el.root.removeAttribute('aria-hidden'); el.root.removeAttribute('inert'); }
  }

  return { onNewRecord, openStartSession };
}
