let audioElement: HTMLAudioElement | null = null

export function getGlobalAudioElement() {
  if (!audioElement) {
    audioElement = new Audio()
    audioElement.preload = 'metadata'
  }

  return audioElement
}
