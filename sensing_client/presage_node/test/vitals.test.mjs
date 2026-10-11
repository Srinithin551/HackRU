import assert from 'node:assert/strict'
import { test } from 'node:test'
import messages from '@smartspectra/node-sdk/messages'
import { BREATH_HOLD_S, PULSE_HOLD_S, VitalsState, toNumber } from '../vitals.mjs'

const { Metrics } = messages.presage.smartspectra
const S = 1000
let ts = 1_760_000_000_000_000   // µs, like Presage's timestamps

const pulse = (value, confidence) => ({ cardio: { pulseRate: [{ value, confidence, stable: confidence >= 40, timestamp: (ts += 1000) }] } })
const breath = (value, confidence) => ({ breathing: { rate: [{ value, confidence, stable: confidence >= 45, timestamp: (ts += 1000) }] } })
/** Through the real protobuf encode/decode, like the SDK's 'metrics' event. */
const wire = (m) => messages.decodeMetrics(Buffer.from(Metrics.encode(m).finish()))

function running() {
  const v = new VitalsState()
  v.active = true
  v.running = true
  v.ingestValidation(0, 'Hold still and record', 0)
  return v
}

test('int64 timestamps decode as Long and convert to numbers', () => {
  const decoded = wire(pulse(72, 60))
  assert.equal(typeof decoded.cardio.pulseRate[0].timestamp, 'object')
  assert.equal(toNumber(decoded.cardio.pulseRate[0].timestamp), ts)
})

test('a pulse becomes a reading with confidence on a 0-1 scale', () => {
  const v = running()
  v.ingestMetrics(wire(pulse(72.04, 61)), 0)
  const r = v.reading(500, 'sid', Date.UTC(2026, 9, 10, 22, 1, 5))
  assert.equal(r.session_id, 'sid')
  assert.equal(r.ts, '2026-10-10T22:01:05.000Z')
  assert.equal(r.heart_rate, 72)
  assert.equal(r.confidence, 0.61)
  assert.equal(r.focus, null)
  assert.equal(r.expression, null)
})

test('the last pulse is held at its own confidence (P2), then drops to no-pulse', () => {
  const v = running()
  v.ingestMetrics(wire(pulse(80, 60)), 0)
  for (const t of [1, 3, 5, 7, PULSE_HOLD_S]) {
    const r = v.reading(t * S, 'sid')
    assert.equal(r.heart_rate, 80, `at ${t}s`)
    assert.equal(r.confidence, 0.6, `at ${t}s: no decay`)
  }
  const gone = v.reading((PULSE_HOLD_S + 1) * S, 'sid')
  assert.equal(gone.heart_rate, null)
  assert.equal(gone.confidence, 0)
})

test('heart rate is a median of the last few seconds, so one bad value does not spike it', () => {
  const v = running()
  v.ingestMetrics(wire(pulse(70, 60)), 0)
  v.ingestMetrics(wire(pulse(140, 60)), 1 * S)
  v.ingestMetrics(wire(pulse(72, 60)), 2 * S)
  assert.equal(v.reading(2.5 * S, 'sid').heart_rate, 72)
})

test('breathing below Presage\'s stable confidence (45) is ignored (P1)', () => {
  const v = running()
  v.ingestMetrics(wire(pulse(70, 60)), 0)
  v.ingestMetrics(wire(breath(48.6, 25)), 0)
  assert.equal(v.reading(1 * S, 'sid').breathing_rate, null)
  v.ingestMetrics(wire(breath(14.2, 55)), 2 * S)
  assert.equal(v.reading(3 * S, 'sid').breathing_rate, 14.2)
  v.ingestMetrics(wire(pulse(70, 60)), 10 * S)
  assert.equal(v.reading(11 * S, 'sid').breathing_rate, 14.2, 'held while it is recent')
  v.ingestMetrics(wire(pulse(70, 60)), (2 + BREATH_HOLD_S) * S)
  assert.equal(v.reading((3 + BREATH_HOLD_S) * S, 'sid').breathing_rate, null, 'dropped after the hold')
})

test('no face (Presage validation) gives an all-null reading right away', () => {
  const v = running()
  v.ingestMetrics(wire(pulse(70, 60)), 0)
  v.ingestValidation(1, 'No face found', 1 * S)
  const r = v.reading(1.5 * S, 'sid')
  assert.deepEqual([r.heart_rate, r.breathing_rate, r.confidence], [null, null, 0])
  assert.deepEqual(v.status(1.5 * S), { state: 'adjust', message: 'No face found. Sit in front of the camera.' })
})

test('the same measurement repeated in a later packet is not counted twice', () => {
  const v = running()
  const m = pulse(70, 60)
  v.ingestMetrics(wire(m), 0)
  v.ingestMetrics(wire(m), 1 * S)
  assert.equal(v.pulse.length, 1)
})

test('status walks idle → starting → warming_up → measuring, and flags weak signals and errors', () => {
  const v = new VitalsState()
  assert.equal(v.status(0).state, 'idle')
  v.active = true
  assert.equal(v.status(0).state, 'starting')
  v.running = true
  assert.equal(v.status(0).state, 'warming_up')
  v.ingestMetrics(wire(pulse(70, 30)), 0)
  assert.deepEqual(v.status(0), { state: 'adjust', message: 'Weak signal. Face a light and hold still.' })
  v.ingestMetrics(wire(pulse(70, 65)), 1 * S)
  assert.deepEqual(v.status(1 * S), { state: 'measuring', message: null })
  v.ingestValidation(12, 'Hold still', 2 * S)
  assert.deepEqual(v.status(2 * S), { state: 'adjust', message: 'Hold still.' })
  v.error = 'Presage rejected the API key. Check PRESAGE_API_KEY in .env.'
  assert.equal(v.status(2 * S).state, 'error')
})

test('reset forgets old values, so a new interview never shows the last one', () => {
  const v = running()
  v.ingestMetrics(wire(pulse(70, 60)), 0)
  v.reset()
  assert.equal(v.reading(1 * S, 'sid').heart_rate, null)
  assert.equal(v.running, false)
})
