#!/usr/bin/env node
/**
 * StressTest Presage sensor: runs Presage's SmartSpectra SDK headless (no window) on the webcam and
 * POSTs one VitalsReading per second to /api/vitals during an interview. It also POSTs a status line to
 * /api/sensor/status ("Hold still", "No face found", …), which the live page shows.
 *
 * Presage (and the camera light) is on only while an interview is active: it starts when the backend
 * reports an active session and stops when the interview ends. The warm-up chat covers Presage's
 * 10-20 s lock-on time.
 *
 * It replaces the SmartSpectra macOS app, its log file and sensing_client/client.py (kept as a fallback:
 * scripts/run_demo.py --legacy-presage). scripts/run_demo.py starts it for you.
 *
 *   node sensing_client/presage_node/sensor.mjs              # real webcam through Presage
 *   node sensing_client/presage_node/sensor.mjs --simulate   # artificial Presage data, no camera (testing)
 *   node sensing_client/presage_node/sensor.mjs --dry-run    # no backend: run Presage now, print readings
 *
 * Settings (environment, else the repo's .env):
 *   PRESAGE_API_KEY          the Presage API key to use first
 *   PRESAGE_BACKUP_API_KEYS  more keys, comma-separated, used in order when a key runs out of credits or is
 *                            rejected; re-read from .env at every switch, so keys can be added without a restart
 *   PRESAGE_CAMERA           part of a camera's name, e.g. "MacBook", when the default camera is the wrong one
 *                            (like an iPhone via Continuity Camera)
 * BACKEND_URL (environment only) defaults to http://localhost:8000.
 * One-time: cd sensing_client/presage_node && npm install
 */
import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import messages from '@smartspectra/node-sdk/messages'
import { VitalsState } from './vitals.mjs'

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..', '..')
const args = new Set(process.argv.slice(2))
const SIMULATE = args.has('--simulate')
const DRY_RUN = args.has('--dry-run')
const BACKEND_URL = (process.env.BACKEND_URL || 'http://localhost:8000').replace(/\/+$/, '')

const SESSION_POLL_MS = 2000
const STATUS_EVERY_MS = 3000     // re-send the status at least this often (a page that just opened gets it)
const MAX_BUFFER = 30            // readings kept while POSTs fail
const HTTP_TIMEOUT_MS = 2000
const RETRY_MS = [3000, 5000, 10000, 20000]   // restart delays after Presage fails
const KEY_RECHECK_MS = 30000     // with every API key spent, look in .env for a new one this often
const RUNNING = 3                // ProcessingStatus.kRunning
const PROCESSING_ERROR = 5       // ProcessingStatus.kError

// Presage error codes (SmartSpectraErrorCode) that mean "this API key is done": move on to the next key.
const KEY_SPENT = { 2: 'was rejected', 4: 'is out of credits' }

// Presage error codes that need the user to act.
const ERROR_HELP = {
  5: 'Presage needs the internet to check the API key. Check the Wi-Fi.',
  7: 'Presage could not open the camera. Allow camera access for your terminal app (System Settings → Privacy & Security → Camera) and close other apps using the camera.',
}

const log = (msg) => console.log(`${new Date().toLocaleTimeString('en-GB')}  ${msg}`)

/** A setting from the environment, else from the repo's .env. */
function setting(name) {
  if (process.env[name]) return process.env[name].trim()
  try {
    const line = readFileSync(join(ROOT, '.env'), 'utf8').split('\n').find((l) => l.trim().startsWith(`${name}=`))
    return line ? line.split('=').slice(1).join('=').trim() : ''
  } catch {
    return ''
  }
}

/**
 * Presage API keys in the order to use them: PRESAGE_API_KEY, then PRESAGE_BACKUP_API_KEYS. Read again at
 * every switch, so a key added to .env later is picked up without a restart.
 */
function apiKeys() {
  const keys = [setting('PRESAGE_API_KEY'), ...setting('PRESAGE_BACKUP_API_KEYS').split(/[\s,]+/)]
  return [...new Set(keys.map((k) => k.trim()).filter(Boolean))]
}

/** "key 2 of 3 (…a1b2)": logs never show a whole key. */
function keyName(key) {
  const keys = apiKeys()
  const i = keys.indexOf(key)
  return `key ${i < 0 ? '?' : i + 1} of ${keys.length} (…${key.slice(-4)})`
}

async function http(method, path, body) {
  const res = await fetch(BACKEND_URL + path, {
    method,
    headers: { 'Content-Type': 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body),
    signal: AbortSignal.timeout(HTTP_TIMEOUT_MS),
  })
  if (!res.ok) throw new Error(`HTTP ${res.status} on ${path}`)
  const text = await res.text()
  return text ? JSON.parse(text) : null
}

/** Feeds a Presage-like source's events into `vitals`, ignoring a source that has been replaced (`current`). */
function wire(source, vitals, onStatus = () => {}, current = () => true) {
  source.on('processingStatus', (status) => {
    if (!current()) return
    vitals.running = status === RUNNING
    if (status === RUNNING) vitals.error = null
    onStatus(status)
  })
  source.on('validationStatus', (code, _ts, hint) => {
    if (current()) vitals.ingestValidation(code, hint, performance.now())
  })
  source.on('metrics', (buf) => {
    if (current()) vitals.ingestMetrics(messages.decodeMetrics(buf), performance.now())
  })
}

/**
 * The real Presage SDK on the webcam, with start/stop, restarts (with backoff) after failures, and API key
 * failover: when Presage rejects a key or its credits run out, a fresh SDK instance takes over with the next
 * key, so measuring only stops once every key is spent.
 */
async function realPresage(vitals) {
  const sdk = await import('@smartspectra/node-sdk')
  const { SmartSpectraSDK, CameraSelection, cardioMetrics, breathingMetrics } = sdk.default ?? sdk

  let cameras = []
  try {
    cameras = SmartSpectraSDK.availableCameras()
    log(`cameras: ${cameras.map((c) => c.name ?? c.id).join(', ') || 'none found'}`)
  } catch (e) {
    log(`couldn't list cameras (${e.message})`)
  }
  let selection = CameraSelection.default
  const wanted = setting('PRESAGE_CAMERA').toLowerCase()
  if (wanted) {
    const match = cameras.find((c) => (c.name ?? '').toLowerCase().includes(wanted) || c.id === wanted)
    if (match) selection = CameraSelection.byId(match.id)
    else log(`PRESAGE_CAMERA="${wanted}" matches no camera; using the default one`)
  }

  const spent = new Set()   // keys Presage refused during this run (out of credits or rejected)
  let key = apiKeys()[0]
  let presage = null        // the SDK instance in use
  let shouldRun = false
  let attempt = 0
  let retryTimer = null
  let recheckTimer = null
  let switching = null      // promise while one SDK instance is swapped for another
  log(`Presage API keys: ${apiKeys().length} (starting with ${keyName(key)})`)

  const create = () => {
    const self = new SmartSpectraSDK({ apiKey: key, requestedMetrics: [...cardioMetrics, ...breathingMetrics] })
    self.useCamera(selection)
    const current = () => presage === self
    wire(self, vitals, (status) => {
      if (status === RUNNING) {
        attempt = 0
        log(`Presage running with API ${keyName(key)}: measuring from the webcam (no window)`)
      } else if (status === PROCESSING_ERROR) {
        restart('processing error')
      }
    }, current)
    self.on('error', (code, message) => {
      if (current()) fail(code, message)
    })
    presage = self
  }

  const begin = () => {
    if (!presage) return
    try {
      presage.start()
    } catch (e) {
      fail(e.code, e.message)
    }
  }

  const restart = (why) => {
    if (!shouldRun || retryTimer || switching || spent.has(key)) return
    vitals.running = false
    const delay = RETRY_MS[Math.min(attempt, RETRY_MS.length - 1)]
    attempt += 1
    log(`Presage stopped (${why}); restarting in ${delay / 1000} s`)
    retryTimer = setTimeout(async () => {
      const inst = presage
      try {
        await inst?.stopAsync()
        inst?.reset()
      } catch {
        // already stopped
      }
      retryTimer = null
      if (shouldRun && presage === inst) begin()   // a key switch meanwhile starts its own instance
    }, delay)
  }

  /** Tears the current SDK instance down and starts a fresh one with `next` (SDK state is process-global). */
  const switchTo = (next) => {
    clearTimeout(retryTimer)
    retryTimer = null
    vitals.running = false
    const old = presage
    presage = null   // from here on the old instance's events are ignored
    switching = (async () => {
      try {
        await new Promise((r) => setImmediate(r))   // leave the SDK callback that reported the failure first
        try {
          await old?.stopAsync()
        } catch {
          // already stopped
        }
        try {
          await old?.destroy()
        } catch {
          // already gone
        }
        key = next
        attempt = 0
        create()
      } catch (e) {
        vitals.error = `Presage error: ${e.message}`
        log(`couldn't switch Presage API keys (${e.message})`)
      } finally {
        switching = null
      }
      if (shouldRun) begin()
    })()
  }

  /** Every key is spent: say so, and look in .env for a newly added key every KEY_RECHECK_MS. */
  const outOfKeys = () => {
    const n = spent.size
    vitals.error = `All ${n} Presage API key${n === 1 ? ' is' : 's are'} out of credits or rejected. ` +
      'Add another key to PRESAGE_BACKUP_API_KEYS in .env.'
    log(`ERROR: ${vitals.error}`)
    clearTimeout(recheckTimer)
    const check = () => {
      const next = apiKeys().find((k) => !spent.has(k))
      if (!next) {
        recheckTimer = setTimeout(check, KEY_RECHECK_MS)
        return
      }
      recheckTimer = null
      log(`found a new Presage API key in .env: switching to ${keyName(next)}`)
      vitals.error = null
      switchTo(next)
    }
    recheckTimer = setTimeout(check, KEY_RECHECK_MS)
  }

  const fail = (code, message) => {
    log(`ERROR ${code}: ${message}`)
    if (KEY_SPENT[code]) {
      spent.add(key)
      const next = apiKeys().find((k) => !spent.has(k))
      if (next) {
        log(`Presage API ${keyName(key)} ${KEY_SPENT[code]}; switching to ${keyName(next)}`)
        switchTo(next)
      } else {
        outOfKeys()
      }
      return
    }
    vitals.error = ERROR_HELP[code] ?? `Presage error: ${message}`
    restart(`error ${code}`)
  }

  create()
  return {
    start() {
      shouldRun = true
      if (switching) return   // a swap in progress starts the new instance itself
      if (!spent.has(key)) {
        begin()
        return
      }
      // This key ran out earlier: use a key added to .env since, or say they're all spent.
      const next = apiKeys().find((k) => !spent.has(k))
      if (next) switchTo(next)
      else outOfKeys()
    },
    async stop() {
      shouldRun = false
      clearTimeout(retryTimer)
      retryTimer = null
      await switching
      try {
        await presage?.stopAsync()
        if (presage?.processingStatus === PROCESSING_ERROR) presage.reset()   // ready for the next interview
      } catch {
        // already stopped
      }
    },
    async destroy() {
      clearTimeout(recheckTimer)
      await switching
      await presage?.destroy()
    },
  }
}

async function simulatedPresage(vitals) {
  const { SimulatedPresage } = await import('./simulate.mjs')
  const sim = new SimulatedPresage()
  wire(sim, vitals)
  log('SIMULATED Presage: artificial vitals, no camera')
  return { start: () => sim.start(), stop: () => sim.stopAsync(), destroy: () => sim.destroy() }
}

async function main() {
  const vitals = new VitalsState()
  let presage = null
  if (SIMULATE) {
    presage = await simulatedPresage(vitals)
  } else if (apiKeys().length) {
    presage = await realPresage(vitals)
  } else {
    log('ERROR: no Presage API key (set PRESAGE_API_KEY in the repo .env)')
  }
  log(DRY_RUN ? 'dry run: not talking to the backend' : `waiting for an interview (backend ${BACKEND_URL})`)

  // Presage runs only during an interview; flipping it on/off is serialized so a quick end + new
  // interview can't overlap a stop with a start.
  let transition = Promise.resolve()
  const setActive = (on) => {
    if (vitals.active === on) return
    vitals.active = on
    transition = transition.then(async () => {
      if (!presage) {
        vitals.error = vitals.active ? 'No Presage API key: set PRESAGE_API_KEY in .env.' : null
        return
      }
      if (on) {
        vitals.reset()
        log('starting Presage')
        presage.start()
      } else {
        log('stopping Presage (camera off until the next interview)')
        await presage.stop()
        vitals.reset()
        vitals.error = null
      }
    })
  }

  let sessionId = DRY_RUN ? 'dry-run' : null
  if (DRY_RUN) setActive(true)
  let buffer = []
  let backendOk = true
  let nextPoll = 0
  let lastStatus = ''
  let lastStatusAt = 0

  const tick = async () => {
    const now = performance.now()
    if (!DRY_RUN && now >= nextPoll) {
      nextPoll = now + SESSION_POLL_MS
      let next = sessionId
      try {
        const active = await http('GET', '/api/sessions/active')
        next = active?.session_id ?? null
        if (!backendOk) log('backend reachable again')
        backendOk = true
      } catch (e) {
        if (backendOk) log(`backend unreachable (${e.message}); retrying`)
        backendOk = false
      }
      if (next !== sessionId) {
        log(next ? `interview started: ${next}` : 'interview ended')
        buffer = []
        sessionId = next
        setActive(Boolean(next))
      }
    }

    const status = { ...vitals.status(performance.now()), simulated: SIMULATE }
    const statusKey = `${status.state}|${status.message}`
    if (!DRY_RUN && backendOk && (statusKey !== lastStatus || now - lastStatusAt >= STATUS_EVERY_MS)) {
      if (statusKey !== lastStatus) log(`status: ${status.state}${status.message ? ` (${status.message})` : ''}`)
      lastStatus = statusKey
      lastStatusAt = now
      http('POST', '/api/sensor/status', status).catch(() => {})
    }
    if (!sessionId) return   // nothing to measure or print between interviews

    const reading = vitals.reading(performance.now(), sessionId)
    let sent = 'dry run'
    if (!DRY_RUN) {
      buffer.push(reading)
      buffer = buffer.slice(-MAX_BUFFER)
      try {
        await http('POST', '/api/vitals', { readings: buffer })
        sent = `sent ${buffer.length}`
        buffer = []
      } catch (e) {
        sent = `POST failed (${e.message}), buffered ${buffer.length}`
      }
    }
    const f = (v, d) => (v === null ? '   --' : v.toFixed(d).padStart(5))
    const pulse = reading.heart_rate === null ? '  NO PULSE' : ''
    console.log(`${new Date().toLocaleTimeString('en-GB')}  HR ${f(reading.heart_rate, 1)}  BR ${f(reading.breathing_rate, 1)}  ` +
      `conf ${reading.confidence.toFixed(2)}${pulse}  | ${status.state}${status.message ? `: ${status.message}` : ''} | ${sent}`)
  }

  let busy = false
  const timer = setInterval(() => {
    if (busy) return   // a slow backend never stacks up ticks
    busy = true
    tick().catch((e) => log(`unexpected error: ${e.message}; continuing`)).finally(() => { busy = false })
  }, 1000)

  const shutdown = async () => {
    clearInterval(timer)
    log('stopping')
    const done = presage ? presage.stop().then(() => presage.destroy()) : Promise.resolve()
    await Promise.race([done, new Promise((r) => setTimeout(r, 4000))])
    process.exit(0)
  }
  process.on('SIGINT', shutdown)
  process.on('SIGTERM', shutdown)
  process.on('SIGHUP', shutdown)
}

main().catch((e) => {
  console.error(e)
  process.exit(1)
})
