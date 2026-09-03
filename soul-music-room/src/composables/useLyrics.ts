import { useEffect } from 'react'
import { getSongLyrics } from '../services/lyrics'
import { usePlayerStore } from '../store/playerStore'
import type { LyricLine } from '../types/music'

const TIME_TAG = /\[(\d{1,3}):(\d{2}(?:\.\d{1,3})?)\]/g

function parseLrc(rawLyrics: string) {
  const entries: Array<{ time: number; text: string }> = []

  for (const rawLine of rawLyrics.split(/\r?\n/)) {
    const tags = [...rawLine.matchAll(TIME_TAG)]
    const text = rawLine.replace(TIME_TAG, '').trim()
    if (tags.length === 0 || !text) continue

    for (const tag of tags) {
      const minutes = Number(tag[1])
      const seconds = Number(tag[2])
      if (!Number.isFinite(minutes) || !Number.isFinite(seconds)) continue
      entries.push({ time: minutes * 60 + seconds, text })
    }
  }

  return entries.sort((left, right) => left.time - right.time)
}

export function parseLyrics(rawLyrics: string, rawTranslation = ''): LyricLine[] {
  const translationByTime = new Map(
    parseLrc(rawTranslation).map((line) => [Math.round(line.time * 1000), line.text]),
  )

  return parseLrc(rawLyrics).map((line) => {
    const translation = translationByTime.get(Math.round(line.time * 1000))
    return translation ? { ...line, translation } : line
  })
}

export function useLyrics() {
  const songId = usePlayerStore((state) => state.currentSong?.id)
  const currentTime = usePlayerStore((state) => state.currentTime)
  const isLyricsOpen = usePlayerStore((state) => state.isLyricsOpen)
  const isFullscreen = usePlayerStore((state) => state.isFullscreen)
  const lyrics = usePlayerStore((state) => state.lyrics)
  const activeLyricIndex = usePlayerStore((state) => state.activeLyricIndex)
  const beginLyricsLoad = usePlayerStore((state) => state.beginLyricsLoad)
  const setLyricsResult = usePlayerStore((state) => state.setLyricsResult)
  const setLyricsError = usePlayerStore((state) => state.setLyricsError)
  const resetLyrics = usePlayerStore((state) => state.resetLyrics)
  const setActiveLyricIndex = usePlayerStore((state) => state.setActiveLyricIndex)
  const shouldLoad = Boolean(songId && (isLyricsOpen || isFullscreen))

  useEffect(() => {
    resetLyrics()
    if (!songId || !shouldLoad) return

    const controller = new AbortController()
    beginLyricsLoad()

    getSongLyrics(songId, controller.signal)
      .then((response) => {
        if (response.status === 'instrumental') {
          setLyricsResult([], 'instrumental')
          return
        }
        if (response.status === 'unavailable') {
          setLyricsResult([], 'empty')
          return
        }

        const parsedLyrics = parseLyrics(response.lyric, response.translation)
        setLyricsResult(parsedLyrics, parsedLyrics.length > 0 ? 'ready' : 'empty')
      })
      .catch((error: unknown) => {
        if (error instanceof DOMException && error.name === 'AbortError') return
        setLyricsError(error instanceof Error ? error.message : '歌词请求失败')
      })

    return () => controller.abort()
  }, [beginLyricsLoad, resetLyrics, setLyricsError, setLyricsResult, shouldLoad, songId])

  useEffect(() => {
    if (lyrics.length === 0) {
      if (activeLyricIndex !== -1) setActiveLyricIndex(-1)
      return
    }

    let nextIndex = -1
    for (let index = 0; index < lyrics.length; index += 1) {
      if (lyrics[index].time > currentTime + 0.05) break
      nextIndex = index
    }

    if (nextIndex !== activeLyricIndex) setActiveLyricIndex(nextIndex)
  }, [activeLyricIndex, currentTime, lyrics, setActiveLyricIndex])
}
