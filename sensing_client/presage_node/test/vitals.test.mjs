import assert from 'node:assert/strict'
import { test } from 'node:test'
import messages from '@smartspectra/node-sdk/messages'
import { BREATH_HOLD_S, HINT_DELAY_MS, HINT_MIN_SHOW_MS, PULSE_COAST_S, PULSE_HOLD_S, VitalsState, toNumber } from '../vitals.mjs'

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
  assert.equal('focus' in r, false)   // there is no focus vital
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
  // Past the hold window it coasts rather than going void (see PULSE_COAST_S), and only reads
  // as "nothing" once the coast is spent.
  const gone = v.reading((PULSE_HOLD_S + PULSE_COAST_S + 1) * S, 'sid')
  assert.equal(gone.heart_rate, null)
  assert.equal(gone.confidence, 0)
})

test('a brief dropout coasts on the last pulse instead of reporting nothing', () => {
  const v = running()
  v.ingestMetrics(wire(pulse(80, 80)), 0)
  // Two seconds past the hold window: still reporting, at reduced confidence, and still above the
  // backend's 0.4 cutoff, so the live chart keeps moving through the blip.
  const blip = v.reading((PULSE_HOLD_S + 2) * S, 'sid')
  assert.equal(blip.heart_rate, 80)
  assert.ok(blip.confidence > 0.4, `confidence ${blip.confidence} should survive a short blip`)
  assert.ok(blip.confidence < 0.8, 'confidence decays while coasting')
  assert.equal(v.coasting((PULSE_HOLD_S + 2) * S), true)
})

test('confidence decays to nothing across the coast, so a real dropout still reads as no signal', () => {
  const v = running()
  v.ingestMetrics(wire(pulse(80, 80)), 0)
  const early = v.reading((PULSE_HOLD_S + 1) * S, 'sid').confidence
  const late = v.reading((PULSE_HOLD_S + PULSE_COAST_S - 1) * S, 'sid').confidence
  assert.ok(early > late, `confidence should fall while coasting (${early} → ${late})`)
  assert.ok(late < 0.4, 'a long dropout drops below the backend cutoff rather than faking a signal')
  assert.equal(v.coasting((PULSE_HOLD_S + PULSE_COAST_S + 1) * S), false)
})

test('a lost face never coasts: an empty chair reports nothing, not a stale pulse', () => {
  const v = running()
  v.ingestMetrics(wire(pulse(80, 80)), 0)
  v.ingestValidation(1, 'No face', (PULSE_HOLD_S + 1) * S)   // kNoFaceFound
  const r = v.reading((PULSE_HOLD_S + 2) * S, 'sid')
  assert.equal(r.heart_rate, null)
  assert.equal(r.confidence, 0)
  assert.equal(v.coasting((PULSE_HOLD_S + 2) * S), false)
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

test('status walks idle → starting → warming_up → measuring; hints wait, then stay up; errors are immediate', () => {
  const weak = { state: 'adjust', message: 'Weak signal. Face a light and hold still.' }
  const v = new VitalsState()
  assert.equal(v.status(0).state, 'idle')
  v.active = true
  assert.equal(v.status(0).state, 'starting')
  v.running = true
  assert.equal(v.status(0).state, 'warming_up')

  v.ingestMetrics(wire(pulse(70, 30)), 0)                      // only a weak pulse
  assert.equal(v.status(0).state, 'warming_up')                 // not shown at once (it used to flicker)...
  assert.deepEqual(v.status(HINT_DELAY_MS), weak)               // ...only once it has lasted
  v.ingestMetrics(wire(pulse(70, 65)), 3 * S)                   // a strong pulse arrives
  assert.deepEqual(v.status(3 * S), weak)                       // the hint stays up long enough to read
  assert.deepEqual(v.status(HINT_DELAY_MS + HINT_MIN_SHOW_MS + 1), { state: 'measuring', message: null })

  v.ingestValidation(12, 'Hold still', 6 * S)
  assert.equal(v.status(6 * S).state, 'measuring')              // a one-second wobble never shows
  v.ingestValidation(0, 'Hold still and record', 7 * S)
  assert.equal(v.status(7 * S).state, 'measuring')
  v.ingestValidation(12, 'Hold still', 8 * S)
  assert.equal(v.status(8 * S).state, 'measuring')
  assert.deepEqual(v.status(8 * S + HINT_DELAY_MS), { state: 'adjust', message: 'Hold still.' })   // a lasting one does

  v.error = 'Presage rejected the API key. Check PRESAGE_API_KEY in .env.'
  assert.equal(v.status(11 * S).state, 'error')
})

test('a weak newest pulse does not replace a strong one from the last 8 s (more usable readings)', () => {
  const v = running()
  v.ingestMetrics(wire(pulse(72, 60)), 0)
  v.ingestMetrics(wire(pulse(90, 25)), 2 * S)
  const r = v.reading(3 * S, 'sid')
  assert.deepEqual([r.heart_rate, r.confidence], [72, 0.6])     // the strong value, at its own confidence
  assert.equal(v.status(3 * S).state, 'measuring')              // and no "weak signal" while it's recent
  // Once no strong pulse is left in the window, the weak one is reported as it is (Data then drops it).
  const later = v.reading((PULSE_HOLD_S + 1) * S, 'sid')
  assert.deepEqual([later.heart_rate, later.confidence], [90, 0.25])
})

test('reset forgets old values, so a new interview never shows the last one', () => {
  const v = running()
  v.ingestMetrics(wire(pulse(70, 60)), 0)
  v.reset()
  assert.equal(v.reading(1 * S, 'sid').heart_rate, null)
  assert.equal(v.running, false)
})
