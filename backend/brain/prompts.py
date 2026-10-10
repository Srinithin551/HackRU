"""Prompt text and context builders for the three Gemini calls. Keep contexts compact (< ~1,500 tokens)."""

from typing import Optional

from backend.brain.history import HistoryItem, num, pct, stats_line
from backend.brain.specs import InterviewSpec
from backend.brain.state import SessionState
from backend.contracts import Baseline, QuestionStats, WindowStats

TRANSCRIPT_LIMIT = 600

SPOKEN_RULES = (
    "Everything you write in question text or spoken_text is read aloud by a text-to-speech voice, so: "
    "plain conversational English, one question at a time, no lists, no code, no markdown, no emoji, "
    "no stage directions, no quotation marks around the whole thing."
)


# ---------------------------------------------------------------- call 1: plan_interview

PLAN_SYSTEM = f"""You are an experienced interviewer preparing a realistic mock interview.
{SPOKEN_RULES}
Each question must be speakable in under 12 seconds (at most about 30 words) and must ask exactly one thing.
Do not number the questions. Do not repeat topics."""


def plan_prompt(spec: InterviewSpec, role: str) -> str:
    subject = "Presentation topic" if spec.key == "presentation" else "Role"
    return (
        f"{subject}: {role}\n"
        f"Interview type: {spec.label}. {spec.style}\n"
        f"Write exactly {spec.n_questions} questions in increasing difficulty, with difficulties "
        f"{', '.join(map(str, spec.difficulties))} in that order (1 = warm-up, 5 = very hard).\n"
        f"For each question give: text (the words spoken aloud), topic (2-4 words), difficulty."
    )


# ---------------------------------------------------------------- call 2: decide_next

DECIDE_SYSTEM = f"""You are a live mock interviewer deciding what to say next, right after the candidate finished an answer.
You also see their body signals measured by webcam (heart rate, breathing, focus, a 0-100 stress score where
< 40 is calm, 40 to 70 rising, > 70 high). Use them to adapt like a perceptive human interviewer would.

Choose exactly one action:
- ease_off: if stress peak > 70 AND answer_quality <= 2. Rephrase the same question more simply or give a small hint, in a warm tone.
- push: if stress mean < 40 AND answer_quality >= 4. Ask a harder follow-up on the same topic.
- follow_up: if the answer was vague but the person seems okay. Ask for one specific example.
- next: otherwise. Move on to the next planned question (the system asks it; leave spoken_text empty).
- wrap_up: only when no planned questions remain and no follow-up is warranted (leave spoken_text empty).
At most one follow-up (ease_off, push, or follow_up) per planned question: if FOLLOW-UPS USED is 1/1, choose next or wrap_up.

spoken_text rules: sounds like a real human interviewer, short and natural, under 30 words, no praise inflation
("great answer!"), never mention heart rate, stress, sensors, or the camera. {SPOKEN_RULES}
answer_quality: 1 to 5, your honest read of the transcript (1 = no real answer, 5 = specific, structured, convincing).
An empty or near-empty transcript is quality 1.
rationale: one short sentence for the logs, never spoken."""


def _baseline_line(b: Optional[Baseline]) -> str:
    if b is None:
        return "unknown"
    return f"HR {b.heart_rate:.0f} bpm, BR {b.breathing_rate:.0f}/min, focus {b.focus:.2f}"


def _transcript(t: str) -> str:
    t = " ".join((t or "").split())
    if not t:
        return "(no answer captured)"
    return t if len(t) <= TRANSCRIPT_LIMIT else t[:TRANSCRIPT_LIMIT] + "…"


def decide_context(s: SessionState, kind: str, question: str, transcript: str,
                   stats: Optional[QuestionStats], window: Optional[WindowStats]) -> str:
    d = s.current_planned.difficulty if s.current_planned else "?"
    if window is not None and window.n_samples:
        now = (f"stress {num(window.stress)}, HR {pct(window.hr_delta_pct)} vs baseline, "
               f"expression {window.dominant_expression or 'n/a'}")
    else:
        now = "no usable vitals"
    return "\n".join([
        f"ROLE: {s.meta.role}   TYPE: {s.spec.label}   TURN: {s.turns}/{s.spec.max_turns}   "
        f"ELAPSED: {s.elapsed():.0f}s of {s.spec.max_seconds}s",
        f"BASELINE: {_baseline_line(s.baseline)}",
        f'QUESTION ({kind}, difficulty {d}): "{question}"',
        f'ANSWER TRANSCRIPT: "{_transcript(transcript)}"',
        f"BODY DURING ANSWER: {stats_line(stats)}",
        f"RIGHT NOW (last {window.seconds if window else 0}s): {now}",
        f"REMAINING PLANNED QUESTIONS: {len(s.queue)}",
        f"FOLLOW-UPS USED ON THIS QUESTION: {s.followups_on_current}/1",
        f"PREVIOUS DECISIONS: {', '.join(s.actions[-3:]) or 'none'}",
    ])


# ---------------------------------------------------------------- call 3: final_report

REPORT_SYSTEM = """You are an interview performance coach writing a post-session report. You have the transcript of every
answer plus webcam-measured body signals (heart rate, breathing, focus, and a 0-100 stress score relative to the
candidate's own calm baseline: < 40 calm, 40 to 70 rising, > 70 high).

Rules:
- Only use numbers that appear in the data below. Never invent or round wildly. Refer to questions as Q1, Q2...
- per_question: one entry for every question listed below (skip calibration), using its exact question_id.
  - what_happened: 1-2 sentences on the content of the answer.
  - body_signal: MUST cite at least one number from the data, e.g. "HR jumped 22% above baseline, stress peaked at 78 twelve seconds in".
    If there were no usable vitals, say so.
  - advice: one concrete thing to do next time on this kind of question.
- composure_score: 0-100, higher is calmer. Start from 100 minus the average stress, then adjust a little for recovery.
- summary: 2-3 sentences, direct and specific, no fluff.
- hardest_moment: the single biggest stress spike, with the question, the numbers, and when it happened.
- top_tips: exactly 3 short, concrete, actionable tips tied to what actually happened.
- Talk to the candidate as "you". Honest but encouraging. No markdown."""


def report_context(s: SessionState, items: list[HistoryItem]) -> str:
    adaptations = {a["question_id"]: a for a in s.adaptations}
    lines = [
        f"CANDIDATE: {s.meta.user_name or 'the candidate'}   ROLE: {s.meta.role}   TYPE: {s.spec.label}",
        f"BASELINE: {_baseline_line(s.baseline)}",
        "",
    ]
    for it in items:
        if it.kind == "calibration":
            continue
        lines.append(f"{it.label} [{it.kind}] question_id={it.question_id}")
        lines.append(f'  QUESTION: "{it.text}"')
        lines.append(f'  ANSWER: "{_transcript(it.transcript)}"')
        lines.append(f"  BODY: {stats_line(it.stats)}")
        a = adaptations.get(it.question_id)
        if a:
            lines.append(f"  INTERVIEWER REACTION: {a['action']} (answer quality {a['answer_quality']}/5)")
    if s.flags:
        labels = {it.question_id: it.label for it in items}
        lines.append("")
        lines.append("LIVE COACH FLAGS SHOWN DURING THE SESSION:")
        for f in s.flags:
            lines.append(f"  {labels.get(f['question_id'], '?')}: {f['level']} (stress {f['stress']:.0f}) \"{f['message']}\"")
    return "\n".join(lines)
