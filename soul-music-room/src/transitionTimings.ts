export const TRANSITION_TIMINGS = {
  airpodsOpening: 3000,
  coverLeaving: 1050,
} as const

export type ExperiencePhase =
  | 'idle'
  | 'airpodsOpening'
  | 'coverLeaving'

export const getPhaseDuration = (
  phase: Exclude<ExperiencePhase, 'idle'>,
  reduceMotion: boolean,
) => (reduceMotion ? 60 : TRANSITION_TIMINGS[phase])
