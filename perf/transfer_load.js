// k6 load test for POST /transfers.
// SLOs (thresholds below): p95 latency < 300 ms, error rate < 1 %.
// Usage:  k6 run -e BASE_URL=http://localhost:8000 -e RATE=50 perf/transfer_load.js
import http from 'k6/http';
import { check } from 'k6';
import { Rate } from 'k6/metrics';

const BASE_URL = __ENV.BASE_URL || 'http://localhost:8000';
const RATE = Number(__ENV.RATE || 50);          // requests per second at steady state
const DURATION = __ENV.DURATION || '60s';
const ACCOUNTS = Number(__ENV.ACCOUNTS || 50);  // fewer accounts = more row-lock contention

export const unexpected = new Rate('unexpected_responses');

export const options = {
  scenarios: {
    steady: {
      executor: 'constant-arrival-rate',
      rate: RATE,
      timeUnit: '1s',
      duration: DURATION,
      preAllocatedVUs: 50,
      maxVUs: 200,
    },
  },
  thresholds: {
    'http_req_duration{name:transfer}': ['p(95)<300'],
    unexpected_responses: ['rate<0.01'],
  },
  summaryTrendStats: ['avg', 'med', 'p(90)', 'p(95)', 'p(99)', 'max'],
};

const json = { headers: { 'Content-Type': 'application/json' } };

export function setup() {
  const ids = [];
  for (let i = 0; i < ACCOUNTS; i++) {
    const acc = http.post(`${BASE_URL}/accounts`, JSON.stringify({ owner: `load-${i}`, daily_limit: 100000000000 }), json).json();
    http.post(`${BASE_URL}/accounts/${acc.id}/deposits`, JSON.stringify({ amount: 2000000000 }), json);
    ids.push(acc.id);
  }
  return { ids };
}

export default function (data) {
  const n = data.ids.length;
  const src = data.ids[Math.floor(Math.random() * n)];
  let tgt = data.ids[Math.floor(Math.random() * n)];
  if (tgt === src) tgt = data.ids[(data.ids.indexOf(src) + 1) % n];
  const res = http.post(
    `${BASE_URL}/transfers`,
    JSON.stringify({ source_id: src, target_id: tgt, amount: 10000 }),
    { headers: { 'Content-Type': 'application/json', 'Idempotency-Key': `${__VU}-${__ITER}-${Date.now()}` }, tags: { name: 'transfer' } },
  );
  // 201 = done, 409 = legitimate business rejection; anything else is unexpected
  const ok = check(res, { 'status is 201 or 409': (r) => r.status === 201 || r.status === 409 });
  unexpected.add(!ok);
}
