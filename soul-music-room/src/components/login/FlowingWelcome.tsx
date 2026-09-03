import { useEffect, useState } from 'react'
import { useReducedMotion } from 'framer-motion'

const welcomeMessages = [
  'Where every emotion finds its melody.',
  'Follow the rhythm, discover your inner world.',
] as const

type AnimationPhase = 'typing' | 'holding' | 'deleting' | 'pause'

function FlowingWelcome() {
  const reduceMotion = Boolean(useReducedMotion())
  const [messageIndex, setMessageIndex] = useState(0)
  const [visibleCharacters, setVisibleCharacters] = useState(0)
  const [phase, setPhase] = useState<AnimationPhase>('typing')
  const currentMessage = welcomeMessages[messageIndex]

  useEffect(() => {
    if (reduceMotion) {
      const reducedMotionTimer = window.setTimeout(() => {
        setMessageIndex((index) => (index + 1) % welcomeMessages.length)
      }, 4500)
      return () => window.clearTimeout(reducedMotionTimer)
    }

    const delay = (() => {
      if (phase === 'typing') {
        return visibleCharacters < currentMessage.length ? 52 : 220
      }
      if (phase === 'holding') return 1900
      if (phase === 'deleting') return visibleCharacters > 0 ? 28 : 220
      return 420
    })()

    const animationTimer = window.setTimeout(() => {
      if (phase === 'typing') {
        if (visibleCharacters < currentMessage.length) {
          setVisibleCharacters((count) => count + 1)
        } else {
          setPhase('holding')
        }
        return
      }

      if (phase === 'holding') {
        setPhase('deleting')
        return
      }

      if (phase === 'deleting') {
        if (visibleCharacters > 0) {
          setVisibleCharacters((count) => count - 1)
        } else {
          setPhase('pause')
        }
        return
      }

      setMessageIndex((index) => (index + 1) % welcomeMessages.length)
      setPhase('typing')
    }, delay)

    return () => window.clearTimeout(animationTimer)
  }, [currentMessage, phase, reduceMotion, visibleCharacters])

  const visibleMessage = reduceMotion
    ? currentMessage
    : currentMessage.slice(0, visibleCharacters)

  return (
    <p className="flowing-welcome" aria-label={currentMessage}>
      <span aria-hidden="true">{visibleMessage}</span>
      <span className="flowing-welcome__cursor" aria-hidden="true" />
    </p>
  )
}

export default FlowingWelcome
