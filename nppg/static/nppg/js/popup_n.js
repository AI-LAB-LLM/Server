/**
 * popup_n.js
 * 기존 ppg popup.js와 동일. LIMIT만 90초로 변경합니다 (조건 5).
 */

import { POPUP_ENABLED } from './state_n.js';

export function initWearPopup() {
  if (!POPUP_ENABLED) {
    document.getElementById('wearPopup')?.classList.add('wear-popup--hidden');
    return { onNewRecord() {}, openStartSession() {} };
  }

  const el = {
    root:    document.getElementById('wearPopup'),
    overlay: document.getElementById('wearOverlay'),
    text:    document.getElementById('wp-text'),
    bar:     document.getElementById('wp-progressbar'),
    sec:     document.getElementById('wp-seconds'),
    close:   document.getElementById('wp-close'),
  };

  const state = { sessionActive: false, startedAt: null, tick: null, forceHidden: false };
  const LIMIT = 90;  // 기존 ppg는 84초, 새 모델은 90초

  const show = () => {
    if (!el.root) return;
    el.root.classList.remove('wear-popup--hidden');
    el.root.removeAttribute('aria-hidden');
    el.overlay?.classList.add('wear-overlay--visible');
    document.body.classList.add('modal-open');
    document.getElementById('appRoot')?.classList.add('blurred');
  };

  const hide = () => {
    if (!el.root) return;
    el.root.classList.add('wear-popup--hidden');
    el.root.setAttribute('aria-hidden', 'true');
    el.overlay?.classList.remove('wear-overlay--visible');
    document.body.classList.remove('modal-open');
    document.getElementById('appRoot')?.classList.remove('blurred');
  };

  const startTick = () => {
    if (state.tick) return;
    state.tick = setInterval(() => {
      if (!state.startedAt) return;
      const sec = Math.min(Math.floor((Date.now() - state.startedAt) / 1000), LIMIT);
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

  el.close?.addEventListener('click', () => {
    state.forceHidden   = true;
    hide();
    clearInterval(state.tick);
    state.tick          = null;
    state.sessionActive = false;
  });

  function openStartSession(startedAtMs) {
    state.sessionActive = true;
    state.forceHidden   = false;
    state.startedAt     = Number.isFinite(startedAtMs) ? startedAtMs : Date.now();

    if (el.text) el.text.innerHTML =
      `베이스라인 측정 중입니다...<br/>시작: <b>${new Date(state.startedAt).toLocaleString()}</b>`;
    if (el.sec) el.sec.textContent = '0';
    if (el.bar) el.bar.style.width = '0%';
    if (state.tick) { clearInterval(state.tick); state.tick = null; }

    show();
    startTick();
  }

  return { onNewRecord() {}, openStartSession };
}