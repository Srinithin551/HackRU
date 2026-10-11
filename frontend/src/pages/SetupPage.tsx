/**
 * Setup (/): name, interview type, an optional file, role → POST /api/sessions (+ /material) → /session/:id.
 * The live page then opens the WebSocket and calls /start.
 *
 * The file (slides, notes, a résumé, a job description) is what Judge draws its questions from. A Class
 * Presentation without one makes Judge open by asking what the presentation covers instead.
 */
import { useRef, useState, type ChangeEvent, type FormEvent } from 'react'
import { useLocation, useNavigate } from 'react-router'
import { createSession, uploadMaterial } from '../api.ts'
import { clearAllFlags } from '../lib/flagStore.ts'
import { Brand, Button } from '../components/ui.tsx'
import { IS_MOCK } from '../lib/config.ts'
import { INTERVIEWER_NAME } from '../lib/constants.ts'
import { INTERVIEW_TYPES, MATERIAL_ACCEPT, MATERIAL_MAX_BYTES, type InterviewType } from '../types.ts'

/** Shown under each type. Counts/limits mirror backend/brain/specs.py. */
const TYPE_DETAILS: Record<InterviewType, string> = {
  software_engineering: '5 questions · about 8 min',
  behavioral: '5 questions · about 8 min',
  class_presentation: '5 questions · Q&A on your talk',
  demo: '3 questions · about 3 min',
}

/** What to import, per type, and what happens without a file. */
const MATERIAL_COPY: Record<InterviewType, { label: string; without: string }> = {
  software_engineering: {
    label: 'Your résumé or the job description',
    without: `Without a file, ${INTERVIEWER_NAME} asks general questions for the role.`,
  },
  behavioral: {
    label: 'Your résumé or the job description',
    without: `Without a file, ${INTERVIEWER_NAME} asks general questions for the role.`,
  },
  class_presentation: {
    label: 'Your slides or notes',
    without: `Without a file, ${INTERVIEWER_NAME} first asks you what your presentation covers, and questions you on that.`,
  },
  demo: {
    label: 'Anything you want to be questioned on',
    without: `Without a file, ${INTERVIEWER_NAME} asks general questions.`,
  },
}

const sizeLabel = (bytes: number) => (bytes < 1024 * 1024 ? `${Math.max(1, Math.round(bytes / 1024))} KB` : `${(bytes / 1024 / 1024).toFixed(1)} MB`)

const hasSpeechRecognition = () =>
  typeof window !== 'undefined' && ('SpeechRecognition' in window || 'webkitSpeechRecognition' in window)

export function SetupPage() {
  const navigate = useNavigate()
  const { search } = useLocation() // keep ?mock=auto
  const [userName, setUserName] = useState('')
  const [interviewType, setInterviewType] = useState<InterviewType>('demo')
  const [roleTitle, setRoleTitle] = useState('')
  const [file, setFile] = useState<File | null>(null)
  const fileInput = useRef<HTMLInputElement | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const isPresentation = interviewType === 'class_presentation'
  const materialCopy = MATERIAL_COPY[interviewType]

  const onPickFile = (e: ChangeEvent<HTMLInputElement>) => {
    const picked = e.target.files?.[0] ?? null
    e.target.value = '' // so picking the same file again after removing it still fires
    if (picked && picked.size > MATERIAL_MAX_BYTES) {
      setError(`${picked.name} is over 10 MB. Pick a smaller file.`)
      return
    }
    setError(null)
    setFile(picked)
  }

  const onSubmit = async (e: FormEvent) => {
    e.preventDefault()
    setBusy(true)
    setError(null)
    try {
      clearAllFlags() // a new trial starts clean: no coach flags from an earlier one
      const { session_id } = await createSession({
        user_name: userName.trim(),
        interview_type: interviewType,
        role_title: roleTitle.trim(),
      })
      if (file) {
        try {
          await uploadMaterial(session_id, file)
        } catch (err) {
          setError(`${INTERVIEWER_NAME} couldn't use ${file.name}: ${err instanceof Error ? err.message : String(err)}. Remove it or pick another file.`)
          setBusy(false)
          return
        }
      }
      navigate({ pathname: `/session/${session_id}`, search })
    } catch (err) {
      setError(`Court couldn't be called to order: ${err instanceof Error ? err.message : String(err)}. Is the backend running on :8000?`)
      setBusy(false)
    }
  }

  return (
    <div className="bg-monitor flex min-h-dvh flex-col">
      <header className="flex h-14 shrink-0 items-center justify-between px-gutter">
        <Brand />
        {IS_MOCK && <span className="label-caps rounded-full border border-hairline px-3 py-1">Mock mode · no backend</span>}
      </header>

      <main className="flex flex-1 items-center justify-center p-gutter">
        <div className="grid w-full max-w-6xl animate-descend items-center gap-10 lg:grid-cols-[1.15fr_1fr] lg:gap-16">
          <section>
            <p className="label-caps">A trial before {INTERVIEWER_NAME}</p>
            <h1 className="mt-4 text-[clamp(2.5rem,1.4rem+3.2vw,4.5rem)] leading-[1.02] font-semibold tracking-tight text-balance">
              Take the stand. <span className="text-ink-muted">Your body testifies too.</span>
            </h1>
            <p className="mt-6 max-w-xl text-lead text-ink-secondary">
              {INTERVIEWER_NAME} questions you aloud while your webcam reads your heart rate and breathing. When it
              has heard enough, a replay shows exactly where your composure slipped and how to hold it.
            </p>
            <dl className="mt-8 grid max-w-xl grid-cols-3 gap-3">
              {[
                ['Heart rate', 'every second'],
                ['Composure', 'vs your baseline'],
                ['Judgement', 'per question'],
              ].map(([k, v]) => (
                <div key={k} className="rounded-control border border-hairline bg-surface-1 px-3 py-3 shadow-[inset_0_1px_0_rgb(255_255_255/0.05)]">
                  <dt className="text-body font-semibold">{k}</dt>
                  <dd className="text-caption text-ink-muted">{v}</dd>
                </div>
              ))}
            </dl>
          </section>

          <form onSubmit={onSubmit} className="panel-gold flex flex-col gap-5 p-panel">
            <label className="flex flex-col gap-2">
              <span className="label-caps">Your name</span>
              <input
                value={userName}
                onChange={(e) => setUserName(e.target.value)}
                placeholder="Alex"
                autoComplete="given-name"
                className="rounded-control border border-border-strong bg-surface-2 px-3.5 py-2.5 text-body text-ink placeholder:text-ink-muted focus:outline-2 focus:outline-accent"
              />
            </label>

            <fieldset className="flex flex-col gap-2">
              <legend className="label-caps mb-2">Trial type</legend>
              <div className="grid grid-cols-2 gap-2">
                {INTERVIEW_TYPES.map((t) => {
                  const active = t.value === interviewType
                  return (
                    <label
                      key={t.value}
                      className={`cursor-pointer rounded-control border px-3.5 py-3 transition-colors ${
                        active ? 'border-accent bg-accent-subtle' : 'border-hairline bg-surface-2 hover:border-border-strong'
                      }`}
                    >
                      <input
                        type="radio"
                        name="interview_type"
                        value={t.value}
                        checked={active}
                        onChange={() => setInterviewType(t.value)}
                        className="sr-only"
                      />
                      <span className="block text-body font-medium">{t.label}</span>
                      <span className="block text-caption text-ink-muted">{TYPE_DETAILS[t.value]}</span>
                    </label>
                  )
                })}
              </div>
            </fieldset>

            {/* Optional: what Judge draws its questions from. Offered once a type is chosen (one always is). */}
            <div className="flex flex-col gap-2">
              <span className="label-caps">Import a file · optional</span>
              <input ref={fileInput} type="file" accept={MATERIAL_ACCEPT} onChange={onPickFile} className="sr-only" tabIndex={-1} />
              {file ? (
                <div className="flex items-center gap-3 rounded-control border border-accent bg-accent-subtle px-3.5 py-2.5">
                  <span className="min-w-0 flex-1">
                    <span className="block truncate text-body font-medium">{file.name}</span>
                    <span className="block text-caption text-ink-muted">
                      {sizeLabel(file.size)} · {INTERVIEWER_NAME} will draw the questions from it
                    </span>
                  </span>
                  <Button type="button" variant="ghost" onClick={() => setFile(null)}>
                    Remove
                  </Button>
                </div>
              ) : (
                <button
                  type="button"
                  onClick={() => fileInput.current?.click()}
                  className="rounded-control border border-dashed border-border-strong bg-surface-2 px-3.5 py-3 text-left transition-colors hover:border-accent focus-visible:outline-2 focus-visible:outline-accent"
                >
                  <span className="block text-body font-medium">{materialCopy.label}</span>
                  <span className="block text-caption text-ink-muted">PDF, PowerPoint, Word or text, up to 10 MB. {materialCopy.without}</span>
                </button>
              )}
            </div>

            <label className="flex flex-col gap-2">
              <span className="label-caps">{isPresentation ? 'Presentation topic' : 'Role you’re practicing for'}</span>
              <input
                value={roleTitle}
                onChange={(e) => setRoleTitle(e.target.value)}
                placeholder={isPresentation ? 'Climate effects on coastal housing' : 'Software Engineer Intern'}
                className="rounded-control border border-border-strong bg-surface-2 px-3.5 py-2.5 text-body text-ink placeholder:text-ink-muted focus:outline-2 focus:outline-accent"
              />
            </label>

            <p className="flex items-start gap-2.5 rounded-control bg-surface-2 px-3.5 py-3 text-caption text-ink-secondary">
              <svg viewBox="0 0 20 20" className="mt-0.5 size-4 shrink-0 fill-none stroke-accent" strokeWidth="1.8" aria-hidden="true">
                <rect x="4" y="9" width="12" height="8" rx="2" />
                <path d="M7 9V6.5a3 3 0 0 1 6 0V9" />
              </svg>
              {/* Accurate on purpose: video never leaves the camera, but answers and vitals are stored for the report. */}
              We&apos;ll read your heart rate and breathing through your webcam. Video is never recorded; your
              answers and vitals are saved to build your report.
            </p>

            {!hasSpeechRecognition() && (
              <p className="text-caption text-rising">{INTERVIEWER_NAME} hears you best in Chrome. In this browser you can type your answers instead.</p>
            )}
            {error && <p className="text-caption text-high">{error}</p>}

            <Button type="submit" disabled={busy} className="py-3 text-lead">
              {busy ? (file ? `${INTERVIEWER_NAME} is reading your file…` : `${INTERVIEWER_NAME} is getting ready…`) : 'Take the stand →'}
            </Button>
          </form>
        </div>
      </main>

      <footer className="px-gutter pb-4 text-center font-mono text-micro text-ink-muted">
        Vitals by Presage · Stored in Tiger Data · Interviewer by Gemini + ElevenLabs
      </footer>
    </div>
  )
}
