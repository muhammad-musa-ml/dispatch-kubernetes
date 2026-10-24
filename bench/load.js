// Open-loop load against Dispatch: a request is started on schedule whether or not earlier ones have
// finished (ramping-arrival-rate), so a stall shows up as failed requests and latency and not as the
// client quietly slowing down. Every iteration is one POST /v1/events with a fresh idempotency key.
//
// Environment: BASE_URL, API_KEY, RUN_ID, STAGES (JSON: [{"duration":"120s","target":60}, ...]),
// START_RATE (arrivals per second at the start), MAX_VUS.
import http from 'k6/http';
import { check } from 'k6';

const stages = JSON.parse(__ENV.STAGES);

export const options = {
  scenarios: {
    steady: {
      executor: 'ramping-arrival-rate',
      startRate: Number(__ENV.START_RATE),
      timeUnit: '1s',
      preAllocatedVUs: 100,
      maxVUs: Number(__ENV.MAX_VUS || 600),
      stages: stages,
    },
  },
  // Keep the console summary short; the harness reads the per-request records from the JSON output.
  summaryTrendStats: ['avg', 'med', 'p(95)', 'max'],
  discardResponseBodies: true,
};

export default function () {
  const key = `${__ENV.RUN_ID}-${__VU}-${__ITER}`;
  const res = http.post(
    `${__ENV.BASE_URL}/v1/events`,
    JSON.stringify({ type: 'bench.event', payload: { run: __ENV.RUN_ID, key: key } }),
    {
      headers: {
        Authorization: `Bearer ${__ENV.API_KEY}`,
        'Idempotency-Key': key,
        'Content-Type': 'application/json',
      },
      timeout: '5s',
    },
  );
  check(res, { 'status is 201': (r) => r.status === 201 });
}
