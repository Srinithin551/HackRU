/**
 * Generates src/mocks/generated_replay.json and src/mocks/generated_report.json by
 * running one full mock session in virtual time (seeded, so the output is
 * byte-identical on every run). They are a matched pair (same question ids), which
 * is what the mock API serves for unknown session ids.
 *
 * Not to be confused with src/mocks/replay_sample.json: that one is Data's export
 * of a real Tiger Data session (the contract sample) and is never overwritten here.
 *
 *   node scripts/gen-fixtures.ts
 *
 * Runs on Node's native type stripping: erasable syntax only, explicit .ts imports.
 */
import { writeFileSync } from 'node:fs'
import { dirname, relative, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { MockSessionEngine } from '../src/mocks/engine.ts'
import { buildReplay } from '../src/mocks/replay.ts'
import { buildReport } from '../src/mocks/report.ts'
import { VirtualScheduler } from '../src/mocks/scheduler.ts'
import { createIdGenerator, seededRng } from '../src/mocks/sim.ts'

const SEED = 20261010
const START = Date.parse('2026-10-10T18:00:00Z')
const MAX_SESSION_MS = 30 * 60_000

const root = resolve(dirname(fileURLToPath(import.meta.url)), '..')
const outDir = resolve(root, 'src/mocks')

const scheduler = new VirtualScheduler(START)
const sessionId = createIdGenerator(seededRng(SEED, 'session'))()
const engine = new MockSessionEngine({
  sessionId,
  userName: 'Shreyas',
  interviewType: 'software_engineering',
  roleTitle: 'Frontend Engineer',
  seed: SEED,
  scheduler,
  autoAnswer: true,
})

engine.start()
const finished = await scheduler.runUntil(() => engine.phase === 'ended', MAX_SESSION_MS)
if (!finished) {
  console.error(`Session did not end within ${MAX_SESSION_MS / 60_000} virtual minutes (phase: ${engine.phase}).`)
  process.exit(1)
}

const recording = engine.getRecording()
const replay = buildReplay(recording)
const report = buildReport(replay, recording.flags)

const write = (name: string, value: unknown) => {
  const path = resolve(outDir, name)
  writeFileSync(path, `${JSON.stringify(value, null, 2)}\n`)
  console.log(`wrote ${relative(root, path)}`)
}
write('generated_replay.json', replay)
write('generated_report.json', report)

const peak = replay.series.reduce((best, p) => Math.max(best, p.stress_peak ?? 0), 0)
const flagLevels = recording.flags.map((f) => f.level).join(', ') || 'none'
const durationS = ((recording.session.ended_at ?? START) - START) / 1000
console.log(
  [
    `session ${sessionId}: ${durationS}s, baseline ${JSON.stringify(replay.session.baseline)}${recording.session.baselineFallback ? ' (fallback)' : ''}`,
    `questions: ${replay.questions.length} (${replay.questions.map((q) => q.kind).join(', ')})`,
    `series: ${replay.series.length} buckets, peak stress ${peak}`,
    `coach flags: ${recording.flags.length} (${flagLevels})`,
    `composure: ${report.composure_score}`,
  ].join('\n'),
)
