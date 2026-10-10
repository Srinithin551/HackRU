"""Prompt text and context builders for the three Gemini calls. Keep contexts compact (< ~1,500 tokens)."""

from typing import Optional

from backend.brain.history import HistoryItem, num, pct, stats_line
from backend.brain.models import PlannedQuestion
from backend.brain.specs import InterviewSpec
from backend.brain.state import SessionState
from backend.contracts import Baseline, QuestionStats, WindowStats

TRANSCRIPT_LIMIT = 600
EXCHANGE_LIMIT = 220      # per side, for the recent-conversation block
RECENT_EXCHANGES = 2
MAX_TURN_WORDS = 45       # what the prompts ask for; interviewer.py rejects anything far longer

SPOKEN_RULES = (
    "Everything you write in question text or spoken_text is read aloud by a text-to-speech voice, so: "
    "plain conversational English with natural contractions (let's, I'd, you're, that's; never \"let us\"), "
    "one question at a time, no lists, no code, no markdown, no emoji, no stage directions, "
    "no quotation marks around the whole thing."
)


# ---------------------------------------------------------------- call 1: plan_interview

PLAN_SYSTEM = f"""You are an experienced interviewer preparing a realistic mock interview.
{SPOKEN_RULES}
Each question must be speakable in under 12 seconds (at most about 30 words) and must ask exactly one thing.
Do not number the questions. Do not repeat topics.
The conversation is already underway: the candidate has been greeted and has done some small talk. So never
greet, welcome, or introduce yourself, and don't open with phrases like "to start off". Each question will be
woven into the live conversation, so write it as a plain question."""


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

DECIDE_SYSTEM = f"""You are a live mock interviewer having a real conversation with the candidate, not reading from a list.
They just finished an answer. Decide what you say next. You also see their body signals measured by webcam
(heart rate, breathing, focus, and a 0-100 stress score where < 40 is calm, 40 to 70 rising, > 70 high).
Use them to adapt like a perceptive human interviewer would.

Choose exactly one action, checking in this order:
- redirect: the answer doesn't address the question. It's off-topic, random, nonsensical, garbled (answers come
  from speech-to-text, so a garbled answer may just be a recognition error), or inappropriate, rude, or clearly not
  a serious attempt. Briefly acknowledge what they said, then steer back to the same question, reworded. If it was
  inappropriate or not a serious attempt, first give a short, calm, professional callout (for example, that an
  answer like that would end a real interview), then redirect. Allowed once per planned question: if REDIRECTS USED
  is 1/1, choose next instead.
- ease_off: stress peak > 70 AND answer_quality <= 2. Rephrase the same question more simply or offer a small hint, warm tone.
- push: stress mean < 40 AND answer_quality >= 4. Ask a harder follow-up on the same topic.
- follow_up: the answer was on topic but vague, and the person seems okay. Ask for one specific example.
- next: otherwise. Move on to the NEXT PLANNED QUESTION.
- wrap_up: only when there is no next planned question and no follow-up is warranted.
ease_off, push, and follow_up share one allowance per planned question: if FOLLOW-UPS USED is 1/1, choose next or
wrap_up instead (redirect is still allowed if unused).

spoken_text is your whole next turn, said out loud as one natural reply:
- Always open by reacting briefly to what they actually said: a few words that show you listened, specific to their
  answer. No praise inflation ("great answer!", "excellent!"), no generic "thanks for sharing".
- If the answer was inappropriate, rude, dismissive, or clearly not a serious attempt, that opening reaction must be
  a short, calm, professional callout, whatever action you choose (even when you move on). For example: "That's not
  the kind of answer that works in an interview; it would cost you the job." Don't soften it into sympathy.
- next: then transition naturally and ask the NEXT PLANNED QUESTION. Keep its meaning, topic, and difficulty. You may
  reword it so it flows from the conversation. Only tie it to something they said when there's a genuine link;
  otherwise use a simple transition ("Let's switch gears.") rather than a forced segue.
- redirect, ease_off, push, follow_up: then ask your one question.
- wrap_up: only the brief reaction (under 15 words) and no question. The closing line is added automatically.
- Under {MAX_TURN_WORDS} words in total. At most one question. Never mention heart rate, stress, sensors, or the camera.
{SPOKEN_RULES}
answer_quality: 1 to 5, your honest read of the transcript (1 = no real answer, 5 = specific, structured, convincing).
An empty, near-empty, or off-topic answer is quality 1.
rationale: one short sentence for the logs, never spoken."""


OPEN_SYSTEM = f"""You are a live mock interviewer. You just finished a little small talk with the candidate and are
now moving into the interview itself. Write what you say next as one natural turn:
- React briefly to something specific they said in the small talk, so it's clear you listened.
- Transition smoothly into the interview and ask the FIRST QUESTION. Keep its meaning, topic, and difficulty. You
  may reword it so it flows from the conversation. Only tie it to something they said when there's a genuine link;
  otherwise use a simple transition ("Let's dig in.") rather than a forced segue.
- If the small talk was rude or not serious, keep the reaction brief and neutral, then move on.
- Don't greet them or say welcome (you already did). Under {MAX_TURN_WORDS} words. Exactly one question.
- Never mention heart rate, stress, sensors, or the camera.
{SPOKEN_RULES}"""


def _baseline_line(b: Optional[Baseline]) -> str:
    if b is None:
        return "unknown"
    return f"HR {b.heart_rate:.0f} bpm, BR {b.breathing_rate:.0f}/min, focus {b.focus:.2f}"


def _clip(t: str, limit: int) -> str:
    t = " ".join((t or "").split())
    return t if len(t) <= limit else t[:limit] + "…"


def _transcript(t: str, limit: int = TRANSCRIPT_LIMIT) -> str:
    return _clip(t, limit) or "(no answer captured)"


def conversation_block(exchanges: list[dict]) -> str:
    """Compact "INTERVIEWER: … / CANDIDATE: …" lines for the given exchanges."""
    out = []
    for e in exchanges:
        out.append(f'  INTERVIEWER: "{_clip(e["text"], EXCHANGE_LIMIT)}"')
        out.append(f'  CANDIDATE: "{_transcript(e["transcript"], EXCHANGE_LIMIT)}"')
    return "\n".join(out) or "  (none)"


def _planned_line(q: Optional[PlannedQuestion]) -> str:
    if q is None:
        return "none (no planned questions left)"
    return f'"{q.text}" (topic: {q.topic}, difficulty {q.difficulty})'


def decide_context(s: SessionState, kind: str, question: str, transcript: str,
                   stats: Optional[QuestionStats], window: Optional[WindowStats]) -> str:
    d = s.current_planned.difficulty if s.current_planned else "?"
    if window is not None and window.n_samples:
        now = (f"stress {num(window.stress)}, HR {pct(window.hr_delta_pct)} vs baseline, "
               f"expression {window.dominant_expression or 'n/a'}")
    else:
        now = "no usable vitals"
    earlier = s.exchanges[:-1][-RECENT_EXCHANGES:]   # the last entry is the answer being judged
    return "\n".join([
        f"ROLE: {s.meta.role}   TYPE: {s.spec.label}   TURN: {s.turns}/{s.spec.max_turns}   "
        f"ELAPSED: {s.elapsed():.0f}s of {s.spec.max_seconds}s",
        f"BASELINE: {_baseline_line(s.baseline)}",
        "EARLIER IN THE CONVERSATION:",
        conversation_block(earlier),
        f'QUESTION JUST ASKED ({kind}, difficulty {d}): "{question}"',
        f'ANSWER TRANSCRIPT: "{_transcript(transcript)}"',
        f"BODY DURING ANSWER: {stats_line(stats)}",
        f"RIGHT NOW (last {window.seconds if window else 0}s): {now}",
        f"NEXT PLANNED QUESTION: {_planned_line(s.queue[0] if s.queue else None)}",
        f"REMAINING PLANNED QUESTIONS (including the next one): {len(s.queue)}",
        f"FOLLOW-UPS USED ON THIS QUESTION: {s.followups_on_current}/1",
        f"REDIRECTS USED ON THIS QUESTION: {s.redirects_on_current}/1",
        f"PREVIOUS DECISIONS: {', '.join(s.actions[-3:]) or 'none'}",
    ])


def open_context(s: SessionState, first: PlannedQuestion) -> str:
    subject = "PRESENTATION TOPIC" if s.spec.key == "presentation" else "ROLE"
    return "\n".join([
        f"{subject}: {s.meta.role}   TYPE: {s.spec.label}",
        "SMALL TALK SO FAR:",
        conversation_block(s.exchanges),
        f"FIRST QUESTION: {_planned_line(first)}",
    ])


# ---------------------------------------------------------------- call 3: final_report

REPORT_SYSTEM = """You are an interview performance coach writing a post-session report. You have the transcript of every
answer plus webcam-measured body signals (heart rate, breathing, focus, and a 0-100 stress score relative to the
candidate's own calm baseline: < 40 calm, 40 to 70 rising, > 70 high).

Rules:
- Only use numbers that appear in the data below. Never invent or round wildly. Refer to questions as Q1, Q2...
- per_question: one entry for every question listed below (skip calibration), using its exact question_id.
  - what_happened: 1-2 sentences on the content of the answer. If it didn't address the question (off-topic, random,
    or inappropriate), say so plainly, and make the advice about answering the question that was asked.
  - body_signal: MUST cite at least one number from the data, e.g. "HR jumped 22% above baseline, stress peaked at 78 twelve seconds in".
    If there were no usable vitals, say so.
  - advice: one concrete thing to do next time on this kind of question.
- composure_score: 0-100, higher is calmer. Start from 100 minus the average stress, then adjust a little for recovery.
  If no question had usable vitals, set it to 50 and say in the summary that composure couldn't be measured.
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
