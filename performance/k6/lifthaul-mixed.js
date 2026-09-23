import http from 'k6/http';
import { check, fail, sleep } from 'k6';
import exec from 'k6/execution';
import { Counter, Rate, Trend } from 'k6/metrics';

// Full mixed-role performance gate. It intentionally refuses mutation traffic unless the
// operator confirms that the target is an isolated synthetic-data environment.
const BASE = __ENV.BASE_URL || 'http://127.0.0.1:8790';
const ALLOW_MUTATIONS = (__ENV.ALLOW_SYNTHETIC_MUTATIONS || '').toLowerCase() === 'true';
const TEST_RUN = __ENV.TEST_RUN_ID || `k6-${Date.now()}`;
const businessErrors = new Rate('business_errors');
const duplicateResults = new Counter('duplicate_financial_or_booking_results');
const bookingLatency = new Trend('booking_submit_ms', true);
const recommendationLatency = new Trend('vehicle_recommendation_ms', true);
const webhookLatency = new Trend('webhook_ack_ms', true);

export const options = {
  scenarios: {
    progressive_capacity: {
      executor: 'ramping-vus',
      startVUs: 0,
      stages: [
        { duration: __ENV.RAMP_100 || '1m', target: 100 },
        { duration: __ENV.HOLD_100 || '2m', target: 100 },
        { duration: __ENV.RAMP_500 || '2m', target: 500 },
        { duration: __ENV.HOLD_500 || '3m', target: 500 },
        { duration: __ENV.RAMP_1000 || '3m', target: 1000 },
        { duration: __ENV.HOLD_1000 || '5m', target: 1000 },
        { duration: __ENV.RAMP_2500 || '4m', target: 2500 },
        { duration: __ENV.HOLD_2500 || '5m', target: 2500 },
        { duration: __ENV.RAMP_5000 || '5m', target: 5000 },
        { duration: __ENV.HOLD_5000 || '5m', target: 5000 },
        { duration: __ENV.RAMP_10000 || '5m', target: 10000 },
        { duration: __ENV.HOLD_10000 || '5m', target: 10000 },
        { duration: '3m', target: 0 },
      ],
      gracefulRampDown: '1m',
    },
  },
  thresholds: {
    http_req_failed: ['rate<0.01'],
    http_req_duration: ['p(95)<2000', 'p(99)<5000'],
    booking_submit_ms: ['p(95)<3000'],
    vehicle_recommendation_ms: ['p(95)<3000'],
    webhook_ack_ms: ['p(95)<2000'],
    business_errors: ['rate<0.01'],
    duplicate_financial_or_booking_results: ['count==0'],
  },
};

function jsonRequest(method, path, body, tags = {}, headers = {}) {
  const params = {
    tags,
    headers: Object.assign({
      'Content-Type': 'application/json',
      Accept: 'application/json',
      'X-Request-ID': `${TEST_RUN}-${exec.vu.idInTest}-${exec.scenario.iterationInTest}`,
    }, headers),
    timeout: '15s',
  };
  return http.request(method, `${BASE}${path}`, body ? JSON.stringify(body) : null, params);
}

function cargo() {
  return {
    contact_name: 'Synthetic Performance Booker', contact_phone: '09170000000',
    origin_island: 'Luzon', dest_island: 'Luzon', cargo_category: 'BOXES_GENERAL',
    cargo: 'Synthetic packaged goods', weight_kg: 750, package_count: 2,
    package_length_cm: 120, package_width_cm: 80, package_height_cm: 80,
    special_handling: [], splittable: false, km: 45,
  };
}

function requireMutationEnvironment() {
  if (!ALLOW_MUTATIONS) {
    fail('Set ALLOW_SYNTHETIC_MUTATIONS=true only for an isolated performance environment.');
  }
}

export default function () {
  const bucket = Math.floor(Math.random() * 100);
  let response;
  let expected = 200;

  if (bucket < 25) { // browse/check rates
    response = jsonRequest('GET', '/public/service-levels', null, { journey: 'browse' });
  } else if (bucket < 40) { // register/sign in
    response = jsonRequest('POST', '/login', {
      identifier: __ENV.STAFF_TEST_ID || 'ops@lifthaul.demo',
      password: __ENV.STAFF_TEST_PASSWORD || 'demo1234',
    }, { journey: 'authentication' });
  } else if (bucket < 55) { // cargo/route entry
    requireMutationEnvironment();
    response = jsonRequest('POST', '/public/bookings/vehicle-recommendations', cargo(), { journey: 'cargo' });
    recommendationLatency.add(response.timings.duration);
  } else if (bucket < 65) { // recommendation
    requireMutationEnvironment();
    response = jsonRequest('POST', '/public/bookings/vehicle-recommendations', cargo(), { journey: 'recommendation' });
    recommendationLatency.add(response.timings.duration);
  } else if (bucket < 75) { // create/update booking
    requireMutationEnvironment();
    const idempotency = `${TEST_RUN}-booking-${exec.vu.idInTest}-${exec.scenario.iterationInTest}`;
    response = jsonRequest('POST', '/public/bookings', Object.assign(cargo(), {
      vehicle: '6w', payment: 'protected', excluded_charges_ack: true,
      idempotency_key: idempotency,
    }), { journey: 'booking' });
    bookingLatency.add(response.timings.duration);
  } else if (bucket < 80) { // protected-payment channel/readiness boundary
    response = jsonRequest('GET', '/public/payments/channels', null, { journey: 'payment' });
  } else if (bucket < 85) { // provider webhook burst; valid fixture required for financial result testing
    requireMutationEnvironment();
    response = jsonRequest('POST', '/webhooks/xendit/payments', {
      id: `${TEST_RUN}-webhook-${exec.vu.idInTest}-${exec.scenario.iterationInTest}`,
      status: 'PAID', external_id: __ENV.PAYMENT_FIXTURE_REF || 'intentionally-unmatched',
    }, { journey: 'webhook' }, { 'X-Callback-Token': __ENV.PAYMENT_WEBHOOK_TOKEN || 'invalid' });
    expected = __ENV.PAYMENT_WEBHOOK_TOKEN ? 200 : 403;
    webhookLatency.add(response.timings.duration);
  } else if (bucket < 90) { // tracking
    const token = __ENV.TRACKING_TOKEN || 'pbk_expected-not-found';
    response = jsonRequest('GET', `/public/bookings/track/${token}`, null, { journey: 'tracking' });
    expected = __ENV.TRACKING_TOKEN ? 200 : 404;
  } else if (bucket < 95) { // upload/POD boundary; use an authenticated fixture in performance env
    response = jsonRequest('GET', '/public/vehicle-variants', null, { journey: 'upload_fixture_boundary' });
  } else if (bucket < 98) { // fleet management read
    response = jsonRequest('GET', '/public/vehicle-variants', null, { journey: 'fleet' });
  } else { // staff administrative sign-in boundary
    response = jsonRequest('POST', '/login', {
      identifier: __ENV.ADMIN_TEST_ID || 'admin@lifthaul.demo',
      password: __ENV.ADMIN_TEST_PASSWORD || 'demo1234',
    }, { journey: 'staff' });
  }

  const ok = check(response, {
    [`expected HTTP ${expected}`]: (r) => r.status === expected,
    'response has request id': (r) => Boolean(r.headers['X-Request-Id'] || r.headers['X-Request-ID']),
  });
  businessErrors.add(!ok);
  sleep(Math.random() * 2 + 0.5);
}
