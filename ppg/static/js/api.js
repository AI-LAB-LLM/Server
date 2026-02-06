console.log("[api.js] LOADED", new Date().toISOString());
const BASE = '/api/records/';

export async function fetchRecordsWithPulses({ deviceId = null, limit = 120, minutes = null } = {}) {
  const params = new URLSearchParams();
  if (deviceId) params.set('device_id', deviceId);
  if (limit)   params.set('limit', String(limit));
  if (minutes) params.set('minutes', String(minutes));
  params.set('t', Date.now().toString());              // ★ 캐시 방지 쿼리

  const url = `${BASE}?${params.toString()}`;
  const res = await fetch(url, { cache: 'no-store' });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  const data = await res.json();
  return (data?.ok && Array.isArray(data.items)) ? data.items : [];
}

export async function fetchEventStatus(deviceId) {
  const params = new URLSearchParams();
  params.set('device_id', deviceId);
  params.set('t', Date.now().toString());

  const res = await fetch(`/api/event_status/?${params.toString()}`, { cache: 'no-store' });
  if (!res.ok) throw new Error(`event_status HTTP ${res.status}`);
  return res.json();
}

export async function startIrBaselineSession(deviceId, startedAtMs = Date.now()) {
  const res = await fetch('/api/baseline/', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      device_id: deviceId || '_default_',
      started_at: new Date(startedAtMs).toISOString(),
    }),
  });
  if (!res.ok) throw new Error(`baseline start failed: ${res.status}`);
  return res.json();
}