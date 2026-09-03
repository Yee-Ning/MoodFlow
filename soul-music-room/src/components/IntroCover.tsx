import { useEffect, useState } from 'react'
import { motion, useReducedMotion } from 'framer-motion'
import earbud from '../assets/earbud.png'
import turntable from '../assets/turntable.png'
import type { ExperiencePhase } from '../transitionTimings'

const welcomeMessage = 'Welcome to Soul Music Room'

function Turntable() {
  return (
    <div className="turntable" aria-label="A pearl white turntable with a vinyl record">
      <img src={turntable} alt="" />
    </div>
  )
}

type IntroCoverProps = {
  phase: Extract<
    ExperiencePhase,
    'idle' | 'airpodsOpening' | 'coverLeaving'
  >
  onExplore: () => void
}

function IntroCover({ phase, onExplore }: IntroCoverProps) {
  const reduceMotion = useReducedMotion()
  const [typedText, setTypedText] = useState(() =>
    window.matchMedia('(prefers-reduced-motion: reduce)').matches
      ? welcomeMessage
      : '',
  )
  const [animationCycle, setAnimationCycle] = useState(0)
  const isOpening = phase !== 'idle'
  const isLeaving = phase === 'coverLeaving'

  useEffect(() => {
    const prefersReducedMotion = window.matchMedia(
      '(prefers-reduced-motion: reduce)',
    ).matches

    if (prefersReducedMotion) return

    let characterIndex = 0
    const typewriter = window.setInterval(() => {
      characterIndex += 1
      setTypedText(welcomeMessage.slice(0, characterIndex))

      if (characterIndex === welcomeMessage.length) {
        window.clearInterval(typewriter)
      }
    }, 72)

    return () => window.clearInterval(typewriter)
  }, [])

  const handleExplore = () => {
    if (isOpening) return

    setAnimationCycle((cycle) => cycle + 1)
    onExplore()
  }

  return (
    <motion.main
      className="intro-cover"
      animate={{ opacity: isLeaving ? 0 : 1 }}
      transition={{
        duration: reduceMotion ? 0.01 : 1.05,
        ease: [0.22, 0.8, 0.24, 1],
      }}
    >
      <motion.div
        className="cover-leaving-background"
        aria-hidden="true"
        animate={{
          opacity: isLeaving ? 1 : 0,
          scale: isLeaving ? 1.045 : 1,
          filter: isLeaving ? 'blur(10px)' : 'blur(0px)',
        }}
        transition={{
          duration: reduceMotion ? 0.01 : 1.05,
          ease: [0.22, 0.8, 0.24, 1],
        }}
      />

      <motion.section
        className="glass-room"
        aria-labelledby="welcome-title"
        animate={{
          opacity: isLeaving ? 0 : 1,
          scale: isLeaving ? 1.025 : 1,
          y: isLeaving ? -6 : 0,
        }}
        transition={{
          duration: reduceMotion ? 0.01 : 0.95,
          ease: [0.22, 0.8, 0.24, 1],
        }}
      >
        <div className="cover-content">
          <Turntable />

          <motion.h1
            id="welcome-title"
            aria-label={welcomeMessage}
            animate={{ opacity: isLeaving ? 0 : 1, y: isLeaving ? -8 : 0 }}
            transition={{ duration: reduceMotion ? 0.01 : 0.65 }}
          >
            <span aria-hidden="true">{typedText}</span>
            <span className="type-caret" aria-hidden="true" />
          </motion.h1>

          <motion.div
            className="explore-area"
            animate={{ opacity: isLeaving ? 0 : 1, y: isLeaving ? 10 : 0 }}
            transition={{ duration: reduceMotion ? 0.01 : 0.6 }}
          >
            <button
              className={`airpods-button${isOpening ? ' is-open' : ''}`}
              type="button"
              onClick={handleExplore}
              disabled={isOpening}
              aria-pressed={isOpening}
              aria-describedby="opening-status"
              aria-label={
                isOpening
                  ? 'Soul Music Room is opening'
                  : 'Explore Soul Music Room'
              }
            >
              <span className="case-lid" aria-hidden="true">
                <span className="case-lid-outer">
                  <span className="case-lid-seam" />
                </span>
                <span className="case-lid-inner">
                  <span className="lid-cavity" />
                </span>
              </span>
              <span className="case-body">
                <span className="case-hinge" aria-hidden="true" />
                <span className="case-interior" aria-hidden="true">
                  <span className="earbud-well earbud-well-left" />
                  <span className="earbud-well earbud-well-right" />
                </span>
                <span className="case-led" aria-hidden="true" />
                <span className="case-label">Explore</span>
              </span>
              <span
                className="earbuds-flight"
                key={animationCycle}
                aria-hidden="true"
              >
                <span className="earbud-track earbud-track-left">
                  <img className="earbud-asset" src={earbud} alt="" />
                </span>
                <span className="earbud-track earbud-track-right">
                  <img className="earbud-asset" src={earbud} alt="" />
                </span>
              </span>
            </button>

            <p
              id="opening-status"
              className={`opening-status${isOpening ? ' is-visible' : ''}`}
              role="status"
              aria-live="polite"
            >
              {isOpening ? 'Opening Soul Music Room...' : '\u00A0'}
            </p>
          </motion.div>
        </div>
      </motion.section>
    </motion.main>
  )
}

export default IntroCover
