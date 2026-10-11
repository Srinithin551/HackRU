/**
 * "What the gods observe": the three signals, sitting directly under the camera in the same column,
 * so the mortal reads them against their own face.
 *
 * Three boxes, not four: heart rate, composure and breath are the signals Presage actually measures.
 * Focus is not among them (sensing_client/presage_node/vitals.mjs sends `focus: null`), so a focus
 * box would be a permanently empty tile.
 */
import { HrSparkline, StressSparkline } from './Sparklines.tsx'
import { StressGauge } from './StressGauge.tsx'
import { StatusDot } from './ui.tsx'
import { useTweenedNumber } from '../hooks/useTweenedNumber.ts'
import type { LiveState } from '../hooks/useLiveSession.ts'
import type { SensorStatusEvent } from '../types.ts'

function sensorLabel(sensorOn: boolean, everHadVitals: boolean, presage: SensorStatusEvent | null): string {
  if (sensorOn) return presage?.simulated ? 'Simulated' : 'Observing'
  if (presage?.state === 'starting' || presage?.state === 'warming_up') return 'Waking the eye…'
  if (presage?.state === 'error') return 'The eye is blind'
  return everHadVitals ? 'Signal lost' : 'Waiting for the eye'
}

function Box({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="panel flex min-w-0 flex-1 flex-col gap-1 p-3.5">
      <span className="label-caps">{title}</span>
      {children}
    </div>
  )
}

export function VitalsRow({
  live,
  sensorOn,
  presage,
}: {
  live: LiveState
  sensorOn: boolean
  presage: SensorStatusEvent | null
}) {
  const v = live.latest
  // Hold the last good number through a brief dropout rather than flashing "—" (the sensor can skip
  // a reading); `sensorOn` already goes false after SENSOR_STALE_MS of real silence.
  const hr = useTweenedNumber(sensorOn ? (v?.heart_rate ?? null) : null)
  const stress = sensorOn ? (v?.stress ?? null) : null
  const breathing = sensorOn ? (v?.breathing_rate ?? null) : null
  const beatSeconds = hr && hr > 20 ? 60 / hr : null

  return (
    <div className="flex shrink-0 flex-col gap-2">
      <div className="flex items-center justify-between px-0.5">
        <span className="label-caps">What the gods observe</span>
        <StatusDot on={sensorOn}>{sensorLabel(sensorOn, live.lastVitalsAt !== null, presage)}</StatusDot>
      </div>

      <div className="flex gap-2">
        <Box title="Heart · bpm">
          <div className="flex items-baseline gap-2">
            <p className="text-metric font-semibold tracking-tight tabular-nums">{hr === null ? '—' : Math.round(hr)}</p>
            {/* One pulse per heartbeat: animation length = 60 / bpm seconds. */}
            <span
              className={`mb-1 size-2.5 shrink-0 rounded-full ${beatSeconds ? 'animate-beat bg-series-hr' : 'bg-ink-disabled'}`}
              style={beatSeconds ? { animationDuration: `${beatSeconds}s` } : undefined}
              aria-hidden="true"
            />
          </div>
          <div className="mt-auto h-10">
            <HrSparkline data={live.vitals} />
          </div>
        </Box>

        <Box title="Composure · 0–100">
          <StressGauge
            score={stress}
            emptyLabel={!sensorOn ? 'No signal' : live.phase === 'calibrating' ? 'Taking your measure…' : 'No reading'}
          />
          <div className="mt-auto h-10">
            <StressSparkline data={live.vitals} />
          </div>
        </Box>

        <Box title="Breath · per min">
          <p className="text-metric font-semibold tracking-tight tabular-nums">
            {breathing === null ? '—' : breathing.toFixed(0)}
          </p>
          <p className="mt-auto text-caption text-ink-muted">
            {breathing === null ? 'The gods wait' : breathing > 20 ? 'Quickened' : 'Steady'}
          </p>
        </Box>
      </div>
    </div>
  )
}
