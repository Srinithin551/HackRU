import assert from 'node:assert/strict'
import { test } from 'node:test'
import messages from '@smartspectra/node-sdk/messages'
import { SimulatedPresage, scenarioAt } from '../simulate.mjs'
import { VitalsState } from '../vitals.mjs'

/** Runs the simulator for `seconds` of fake time and returns one reading per second. */
function run(seconds) {
  const sim = new SimulatedPresage({ seed: 7 })
  const vitals = new VitalsState()
  vitals.active = true
  vitals.running = true
  let now = 0
  sim.on('validationStatus', (code, _ts, hint) => vitals.ingestValidation(code, hint, now))
  sim.on('metrics', (buf) => vitals.ingestMetrics(messages.decodeMetrics(buf), now))
  sim.restart(0)
  const readings = []
  for (now = 0; now <= seconds * 1000; now += 250) {
    sim.step(now)
    if (now % 1000 === 0) readings.push({ t: now / 1000, ...vitals.reading(now, 'sid'), status: vitals.status(now) })
  }
  return readings
}

const hr = (rows) => rows.map((r) => r.heart_rate).filter((x) => x !== null)
const mean = (xs) => xs.reduce((a, b) => a + b, 0) / xs.length

test('scenario: no face, warm-up, calm calibration, then stress spikes', () => {
  assert.equal(scenarioAt(1).validation, 1)
  assert.equal(scenarioAt(5).warming, true)
  assert.equal(scenarioAt(30).stressed, 0)
  assert.equal(scenarioAt(50 + 45).stressed, 1)
  assert.equal(scenarioAt(50 + 13).validation, 12)
  assert.equal(scenarioAt(50 + 60 + 24).validation, 1, 'face lost in every other cycle')
})

test('simulated readings look like Presage through the real decode path', () => {
  const rows = run(170)
  const at = (a, b) => rows.filter((r) => r.t >= a && r.t < b)

  assert.ok(at(0, 3).every((r) => r.heart_rate === null && r.confidence === 0), 'no face at first')
  assert.ok(at(0, 3).every((r) => r.status.message?.startsWith('No face')))
  assert.ok(at(4, 9).every((r) => r.heart_rate === null && r.status.state === 'warming_up'))

  const calm = hr(at(15, 50))
  assert.ok(calm.length >= 30, `calibration mostly has a pulse (${calm.length}/35)`)
  assert.ok(mean(calm) > 66 && mean(calm) < 75, `calm HR ~70 (${mean(calm)})`)

  const spike = hr(at(50 + 40, 50 + 55))
  assert.ok(mean(spike) > 90, `stressed HR ~98 (${mean(spike)})`)

  // Breathing: junk values at 10% confidence (45 br/min) never show up.
  const br = rows.map((r) => r.breathing_rate).filter((x) => x !== null)
  assert.ok(br.length > 100)
  assert.ok(br.every((x) => x < 30), 'no junk breathing values')

  // Weak pulses (30%) come now and then, but a strong one from the last few seconds covers them, so almost
  // every reading clears Data's 0.4 cutoff (only coasting through a gap can dip below it).
  const withPulse = rows.filter((r) => r.heart_rate !== null)
  assert.ok(withPulse.filter((r) => r.confidence >= 0.4).length >= 0.95 * withPulse.length)
  assert.ok(rows.some((r) => r.status.message === 'Hold still.'))
  assert.ok(at(50 + 60 + 22, 50 + 60 + 26).every((r) => r.heart_rate === null), 'face lost mid-interview')
})
