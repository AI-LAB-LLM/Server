// apnea/static/apnea/js/api.js
// 기존 ppg 앱의 api.js와 동일한 구조, URL만 /apnea/api/로 변경

const BASE = '/apnea/api/records/';

export async function fetchRecordsWithPulses({ deviceId = null, limit = 120, minutes = null } = {}) {
  const params = new URLSearchParams();
  if (deviceId) params.set('device_id', deviceId);
  if (limit)   params.set('limit', String(limit));
  if (minutes) params.set('minutes', String(minutes));
  params.set('t', Date.now().toString());

  const url = `${BASE}?${params.toString()}`;
  const res = await fetch(url, { cache: 'no-store' });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  const data = await res.json();
  return (data?.ok && Array.isArray(data.items)) ? data.items : [];
}

export async function startBaselineSession(deviceId, startedAtMs = Date.now()) {
  const res = await fetch('/apnea/api/baseline/', {
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

export async function fetchModelStatus() {
  const res = await fetch('/apnea/api/status/', { cache: 'no-store' });
  if (!res.ok) throw new Error(`status HTTP ${res.status}`);
  return res.json();
}

export async function fetchBaselines(deviceId) {
    const params = new URLSearchParams();

    params.set('device_id', deviceId);
    params.set('t', Date.now().toString());

    const res = await fetch(
        `/apnea/api/baselines/?${params.toString()}`,
        {
            cache: 'no-store',
        }
    );

    const data = await res.json();

    if (!res.ok || !data.ok) {
        throw new Error(
            data?.error ||
            `baseline list failed: ${res.status}`
        );
    }

    return data;
}


export async function selectBaseline(
    deviceId,
    sessionId
) {
    const res = await fetch(
        '/apnea/api/baselines/select/',
        {
            method: 'POST',

            headers: {
                'Content-Type': 'application/json',
            },

            body: JSON.stringify({
                device_id: deviceId,
                session_id: sessionId,
            }),
        }
    );

    const data = await res.json();

    if (!res.ok || !data.ok) {
        throw new Error(
            data?.error ||
            `baseline select failed: ${res.status}`
        );
    }

    return data;
}
