/**
 * api_n.js
 * 기존 ppg api.js와 동일한 구조.
 * 엔드포인트만 /nppg/api로 변경합니다.
 */

const BASE = window.NPPG_API || '/nppg/api';

export async function fetchRecordsWithPulses({ deviceId = null, limit = 120, minutes = null } = {}) {
  const params = new URLSearchParams();
  if (deviceId) params.set('device_id', deviceId);
  if (limit)    params.set('limit', String(limit));
  if (minutes)  params.set('minutes', String(minutes));
  params.set('t', Date.now());  // 캐시 방지

  const res = await fetch(`${BASE}/records/?${params}`, { cache: 'no-store' });
  if (!res.ok) throw new Error(`records fetch failed: ${res.status}`);
  const data = await res.json();
  return (data?.ok && Array.isArray(data.items)) ? data.items : [];
}

export async function startBaselineSession(deviceId) {
  const res = await fetch(`${BASE}/baseline/`, {
    method:  'POST',
    headers: { 'Content-Type': 'application/json' },
    body:    JSON.stringify({ device_id: deviceId || '_default_' }),
  });
  if (!res.ok) throw new Error(`baseline start failed: ${res.status}`);
  return res.json();
}

export async function getBaselineStatus(deviceId) {
  const params = new URLSearchParams({ device_id: deviceId || '_default_', t: Date.now() });
  const res = await fetch(`${BASE}/baseline/?${params}`, { cache: 'no-store' });
  if (!res.ok) throw new Error(`baseline status failed: ${res.status}`);
  return res.json();
}

export async function fetchEventStatus(deviceId) {
  const params = new URLSearchParams({ device_id: deviceId, t: Date.now() });
  const res = await fetch(`${BASE}/event-status/?${params}`, { cache: 'no-store' });
  if (!res.ok) return null;
  return res.json();
}