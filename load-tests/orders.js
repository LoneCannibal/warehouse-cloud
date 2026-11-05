// Sustained order traffic for the HPA demo. Requirement (c).
//
//   k6 run -e BASE_URL=http://<alb-dns-name> load-tests/orders.js
//
// Options: PEAK_RPS (default 60), RAMP (default 2m), HOLD (default 10m).
//
// The arrival-rate executor keeps offering orders at the target rate even if the
// system slows down. A fixed number of virtual users would back off instead, and
// would hide the overload that makes the HPA scale out.
import http from 'k6/http';
import { check } from 'k6';
import { Counter } from 'k6/metrics';

const BASE_URL = __ENV.BASE_URL || 'http://localhost:8080';
const PEAK_RPS = Number(__ENV.PEAK_RPS || 60);
const RAMP = __ENV.RAMP || '2m';
const HOLD = __ENV.HOLD || '10m';

const ZONES = ['A', 'B', 'C'];
const SKUS_PER_ZONE = 100;

const ordersAccepted = new Counter('orders_accepted');
const ordersRefused = new Counter('orders_refused');

export const options = {
  scenarios: {
    sustained_orders: {
      executor: 'ramping-arrival-rate',
      startRate: 2,
      timeUnit: '1s',
      preAllocatedVUs: 50,
      maxVUs: 400,
      stages: [
        { duration: RAMP, target: PEAK_RPS },
        { duration: HOLD, target: PEAK_RPS },
        { duration: RAMP, target: 2 },
      ],
    },
  },
  thresholds: {
    http_req_failed: ['rate<0.01'],
    http_req_duration: ['p(95)<1000'],
  },
};

function randomInt(limit) {
  return Math.floor(Math.random() * limit);
}

function randomLine(zone) {
  const number = String(randomInt(SKUS_PER_ZONE)).padStart(3, '0');
  return { sku: `SKU-${zone}${number}`, quantity: 1 + randomInt(3) };
}

export default function () {
  const zones = ZONES.filter(() => Math.random() < 0.6);
  const lines = (zones.length ? zones : ['A']).map(randomLine);
  const body = JSON.stringify({
    customer: `load-${__VU}`,
    priority: randomInt(3),
    lines,
  });

  const response = http.post(`${BASE_URL}/v1/orders`, body, {
    headers: { 'Content-Type': 'application/json' },
    tags: { name: 'POST /v1/orders' },
  });

  const accepted = check(response, { 'order accepted': (r) => r.status === 202 });
  if (accepted) {
    ordersAccepted.add(1);
  } else {
    ordersRefused.add(1);
  }
}
