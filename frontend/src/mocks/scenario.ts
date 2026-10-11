/**
 * Canned interviews for the mock backend: what Gemini's plan_interview would
 * return, which answers trigger a body reaction, and what the "user" says in
 * auto mode.
 *
 * DOM-free.
 */
import type { InterviewType } from '../types.ts'
import type { OverlayKind } from './sim.ts'

export interface PlannedQuestion {
  /** Spoken aloud: under 30 words, no markdown. */
  text: string
  topic: string
  /** 1 to 5. */
  difficulty: number
  /** Body reaction while answering, anchored to the answer start. */
  overlay: OverlayKind | null
  /** How far into the answer the overlay starts. */
  overlayOffsetMs: number
  /** Ease-off follow-up Brain asks if the answer spikes stress (> 70). */
  followUp: string | null
  /** Canned transcript for auto mode. */
  answer: string
  /** Auto-mode answer length. */
  answerMs: number
  followUpAnswer: string | null
  followUpAnswerMs: number
}

export interface Scenario {
  calibration: { text: string; answer: string; answerMs: number }
  questions: PlannedQuestion[]
}

/** backend/brain/lines.py CALIBRATION_LINE. */
export const CALIBRATION_LINE = 'Thanks for joining me today. To start, tell me a little about yourself.'
export const CLOSING_LINE = "That's everything I had. Thanks for walking me through all of that, your report is coming up now."

const CALIBRATION_ANSWER =
  "Sure. I'm a computer science student, and most of what I build is backend work, APIs and data pipelines. " +
  "Lately I've been working on a project that streams sensor data to a live dashboard, which has been a lot of fun."

type Draft = Omit<PlannedQuestion, 'followUp' | 'followUpAnswer' | 'followUpAnswerMs' | 'overlayOffsetMs'> &
  Partial<Pick<PlannedQuestion, 'followUp' | 'followUpAnswer' | 'followUpAnswerMs' | 'overlayOffsetMs'>>

const q = (draft: Draft): PlannedQuestion => ({
  followUp: null,
  followUpAnswer: null,
  followUpAnswerMs: 22_000,
  overlayOffsetMs: 5_000,
  ...draft,
})

const SOFTWARE_ENGINEERING: PlannedQuestion[] = [
  q({
    text: 'Walk me through a project you shipped recently and the part you personally owned.',
    topic: 'experience',
    difficulty: 2,
    overlay: null,
    answer:
      'I built the dashboard for a campus food pantry. I owned the React frontend and the inventory API, and we cut ' +
      'the time volunteers spent on stock counts from an hour to about ten minutes.',
    answerMs: 26_000,
  }),
  q({
    text: 'How would you debug a page that suddenly takes eight seconds to load in production?',
    topic: 'debugging',
    difficulty: 3,
    overlay: 'face_lost',
    overlayOffsetMs: 9_000,
    answer:
      'First I would check whether it is the network or the main thread using the performance panel, then compare ' +
      'with the last deploy. Usually it is a new bundle or an API call that lost its cache.',
    answerMs: 27_000,
  }),
  q({
    text: 'Tell me about a time you disagreed with a teammate on a technical decision. What happened?',
    topic: 'collaboration',
    difficulty: 3,
    overlay: 'mild',
    overlayOffsetMs: 6_000,
    answer:
      'We disagreed about using a global store versus local state. I wrote a small prototype of both, we compared ' +
      'them on the real screens, and we went with local state plus one context.',
    answerMs: 28_000,
  }),
  q({
    text: 'How would you design a rate limiter for an API that serves ten thousand requests per second?',
    topic: 'system design',
    difficulty: 4,
    overlay: null,
    answer:
      'I would use a token bucket per API key stored in Redis, with a Lua script so the check and decrement are atomic, ' +
      'and return a retry after header when the bucket is empty.',
    answerMs: 30_000,
  }),
  q({
    text: 'Explain how you would find the longest substring without repeating characters, and its time complexity.',
    topic: 'algorithms',
    difficulty: 5,
    overlay: 'spike',
    overlayOffsetMs: 5_000,
    followUp: "No rush. Let's simplify it: what would you keep track of as you move through the string one character at a time?",
    answer:
      'Okay so, um, I would check every substring, no wait, that is too slow. Maybe a hash map of characters... ' +
      'I think there is a window you slide, but I am blanking on how to move the left side.',
    answerMs: 30_000,
    followUpAnswer:
      'I would keep the last index of each character in a map and a left pointer. When I see a repeat, I jump left past ' +
      'it. That is one pass, so linear time.',
    followUpAnswerMs: 24_000,
  }),
]

const BEHAVIORAL: PlannedQuestion[] = [
  q({
    text: 'Tell me about something you worked on that you are genuinely proud of.',
    topic: 'motivation',
    difficulty: 2,
    overlay: null,
    answer:
      'I organized a peer tutoring program for intro programming. We went from five tutors to twenty and the course ' +
      'drop rate went down that semester.',
    answerMs: 25_000,
  }),
  q({
    text: 'Describe a time you had to deliver something on a deadline that felt impossible.',
    topic: 'pressure',
    difficulty: 3,
    overlay: 'face_lost',
    overlayOffsetMs: 9_000,
    answer:
      'For a class project our API partner dropped out a week before the demo. I cut scope to the two features that ' +
      'mattered, mocked the rest, and we demoed on time.',
    answerMs: 27_000,
  }),
  q({
    text: 'Tell me about a time you received critical feedback. How did you respond?',
    topic: 'growth',
    difficulty: 3,
    overlay: 'mild',
    overlayOffsetMs: 6_000,
    answer:
      'A manager told me my updates were too long. I started writing three bullet summaries at the top, and she ' +
      'mentioned in my review that it helped.',
    answerMs: 26_000,
  }),
  q({
    text: 'Describe a conflict on a team where you were partly at fault. What would you do differently?',
    topic: 'conflict',
    difficulty: 4,
    overlay: null,
    answer:
      'I merged a change without telling the person who owned that module and it broke their work. I apologized, ' +
      'fixed it with them, and now I always ping the owner first.',
    answerMs: 28_000,
  }),
  q({
    text: 'Tell me about your biggest failure, and what it says about how you make decisions.',
    topic: 'failure',
    difficulty: 5,
    overlay: 'spike',
    overlayOffsetMs: 5_000,
    followUp: "That's a tough one, take a second. Just pick one decision from that time: what did you know when you made it?",
    answer:
      'Um, my biggest failure, I guess, there was a startup idea, or actually a class, I am not sure which one to ' +
      'pick. I think I tend to, I do not know, overcommit maybe.',
    answerMs: 30_000,
    followUpAnswer:
      'I signed up to lead two projects in the same month because both sounded exciting, without checking the ' +
      'calendar. Now I write down the hours before I say yes.',
    followUpAnswerMs: 22_000,
  }),
]

const CLASS_PRESENTATION: PlannedQuestion[] = [
  q({
    text: 'Give me the one-sentence version of your presentation topic.',
    topic: 'framing',
    difficulty: 2,
    overlay: null,
    answer:
      'My presentation is about how campus buses could use live ridership data to cut wait times at peak hours.',
    answerMs: 22_000,
  }),
  q({
    text: 'What is the strongest piece of evidence behind your main claim?',
    topic: 'evidence',
    difficulty: 3,
    overlay: 'face_lost',
    overlayOffsetMs: 9_000,
    answer:
      'We counted riders at four stops for two weeks. The busiest stop had people waiting over fifteen minutes during ' +
      'the noon rush while the next stop had empty buses.',
    answerMs: 27_000,
  }),
  q({
    text: 'Which part of your slides do you think the audience will find most confusing?',
    topic: 'audience',
    difficulty: 3,
    overlay: 'mild',
    overlayOffsetMs: 6_000,
    answer:
      'Probably the scheduling model. I plan to show one bus route as an animation before showing the full chart.',
    answerMs: 24_000,
  }),
  q({
    text: 'How would you respond if someone said your sample size is too small to mean anything?',
    topic: 'defense',
    difficulty: 4,
    overlay: null,
    answer:
      'I would agree it is a pilot, explain that the pattern held every single weekday, and propose the larger ' +
      'study the transit office could run with their card data.',
    answerMs: 27_000,
  }),
  q({
    text: 'Your professor interrupts and says your conclusion does not follow from your data. Defend it right now.',
    topic: 'pressure',
    difficulty: 5,
    overlay: 'spike',
    overlayOffsetMs: 5_000,
    followUp: "Okay, let's slow down. Which single number from your data best supports your conclusion?",
    answer:
      'Well, I, um, the data does show the waits, I think, and the conclusion is more of a, a suggestion, so maybe ' +
      'it is not a full conclusion. I would have to look at the slide again.',
    answerMs: 30_000,
    followUpAnswer:
      'The fifteen minute average wait at the busiest stop, compared with four minutes at the next one. That gap is ' +
      'the whole case for rebalancing.',
    followUpAnswerMs: 20_000,
  }),
]

const DEMO: PlannedQuestion[] = [
  q({
    text: 'Tell me about yourself in thirty seconds.',
    topic: 'introduction',
    difficulty: 2,
    overlay: null,
    answer:
      'I am a junior studying computer science. I like building tools people actually use, and this weekend I am ' +
      'working on an interview coach that reads your heart rate.',
    answerMs: 22_000,
  }),
  q({
    text: 'Without any code, explain how a hash map handles two keys that land in the same bucket.',
    topic: 'data structures',
    difficulty: 5,
    overlay: 'spike',
    overlayOffsetMs: 5_000,
    followUp: "No pressure. Think of a coat check with one hook per number: what happens when two coats get the same number?",
    answer:
      'Um, so the hash, it, the keys go into buckets and if two go in the same one then, I think it just, it ' +
      'overwrites? No. Sorry, I am blanking on this.',
    answerMs: 30_000,
    followUpAnswer:
      'Oh, you hang both coats on that hook. So the bucket keeps a small list, and on lookup you walk that list and ' +
      'compare the actual keys.',
    followUpAnswerMs: 20_000,
  }),
  q({
    text: 'Last one. What is something you learned this weekend that surprised you?',
    topic: 'reflection',
    difficulty: 2,
    overlay: null,
    answer:
      'That the hardest part of a live demo is not the code, it is making every piece fail gracefully. We spent ' +
      'hours on fallbacks.',
    answerMs: 20_000,
  }),
]

const BANKS: Record<InterviewType, PlannedQuestion[]> = {
  software_engineering: SOFTWARE_ENGINEERING,
  behavioral: BEHAVIORAL,
  class_presentation: CLASS_PRESENTATION,
  demo: DEMO,
}

/** The canned plan for an interview type (unknown types fall back to demo). */
export function getScenario(interviewType: InterviewType): Scenario {
  const questions = BANKS[interviewType] ?? DEMO
  return {
    calibration: { text: CALIBRATION_LINE, answer: CALIBRATION_ANSWER, answerMs: 38_000 },
    questions: questions.map((question) => ({ ...question })),
  }
}
