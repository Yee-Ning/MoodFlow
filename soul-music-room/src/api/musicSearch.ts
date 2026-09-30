import { apiRequest, BackendApiError } from './client'
import type { AlbumDetail, MusicEntity, MusicEntityType, SongSummary } from '../types/music'

export const ENTITY_TYPES: MusicEntityType[] = ['song', 'artist', 'album', 'playlist']
export type SearchCategory = MusicEntityType | 'all'
export type SearchResults = Record<MusicEntityType, MusicEntity[]>
export type SearchErrors = Partial<Record<MusicEntityType, string>>

export function parseSearchCategory(value: string | null): SearchCategory {
  return ENTITY_TYPES.includes(value as MusicEntityType) ? value as MusicEntityType : 'all'
}

export function searchUrl(query: string, category: SearchCategory = 'all') {
  const params = new URLSearchParams()
  if (query.trim()) params.set('q', query.trim())
  if (category !== 'all') params.set('type', category)
  return `/search${params.size ? `?${params.toString()}` : ''}`
}

export function emptySearchResults(): SearchResults {
  return { song: [], artist: [], album: [], playlist: [] }
}

export async function searchEntities(
  query: string, type: MusicEntityType, limit: number, signal?: AbortSignal,
) {
  const keyword = query.trim()
  if (!keyword || keyword.length > 200) throw new BackendApiError('请输入 1 到 200 个字符的搜索关键词', 400)
  if (!ENTITY_TYPES.includes(type) || !Number.isInteger(limit) || limit < 1 || limit > 50) {
    throw new BackendApiError('搜索参数无效', 400)
  }
  const params = new URLSearchParams({ keyword, type, limit: String(limit) })
  const entities = await apiRequest<MusicEntity[]>(`/api/music/entities/search?${params}`, signal)
  if (!Array.isArray(entities) || entities.some((entity) =>
    !entity || !Number.isSafeInteger(entity.id) || entity.id <= 0
    || entity.entityType !== type || typeof entity.name !== 'string' || !entity.name.trim()
    || typeof entity.artist !== 'string' || typeof entity.album !== 'string'
    || typeof entity.coverUrl !== 'string' || !Array.isArray(entity.relatedEntities)
    || !entity.metadata || typeof entity.metadata !== 'object' || Array.isArray(entity.metadata)
    || entity.relatedEntities.some((reference) => !reference || typeof reference.name !== 'string'),
  )) throw new BackendApiError('音乐搜索返回了无效的实体数据')
  return entities
}

export async function searchCatalog(query: string, category: SearchCategory, signal?: AbortSignal) {
  const types = category === 'all' ? ENTITY_TYPES : [category]
  const responses = await Promise.allSettled(
    types.map((type) => searchEntities(query, type, category === 'all' ? 8 : 50, signal)),
  )
  if (signal?.aborted) throw new DOMException('Search cancelled', 'AbortError')
  const results = emptySearchResults()
  const errors: SearchErrors = {}
  responses.forEach((response, index) => {
    const type = types[index]
    if (response.status === 'fulfilled') results[type] = response.value
    else errors[type] = response.reason instanceof Error ? response.reason.message : '搜索暂时不可用，请重试'
  })
  return { results, errors }
}

export function songFromEntity(entity: MusicEntity): SongSummary {
  if (entity.entityType !== 'song') throw new BackendApiError('该实体不是歌曲', 400)
  const artists = entity.relatedEntities.filter((item) => item.entityType === 'artist').map((item) => item.name)
  const duration = entity.metadata.duration
  return {
    id: entity.id, name: entity.name,
    artists: artists.length ? artists : entity.artist ? [entity.artist] : [],
    album: entity.album, coverUrl: entity.coverUrl,
    duration: typeof duration === 'number' && Number.isFinite(duration) ? Math.max(0, duration) : 0,
  }
}

export async function getAlbumDetail(id: number, signal?: AbortSignal) {
  if (!Number.isSafeInteger(id) || id <= 0) throw new BackendApiError('专辑地址无效', 400)
  const detail = await apiRequest<AlbumDetail>(`/api/albums/${id}`, signal)
  if (!detail?.album || detail.album.id !== id || detail.album.entityType !== 'album' || !Array.isArray(detail.songs)
    || detail.songs.some((song) => !song || !Number.isSafeInteger(song.id) || song.id <= 0
      || typeof song.name !== 'string' || !song.name.trim() || !Array.isArray(song.artists)
      || song.artists.some((artist) => typeof artist !== 'string') || typeof song.album !== 'string'
      || typeof song.coverUrl !== 'string' || typeof song.duration !== 'number'
      || !Number.isFinite(song.duration) || song.duration < 0)) {
    throw new BackendApiError('音乐服务返回了不匹配的专辑数据')
  }
  return detail
}
