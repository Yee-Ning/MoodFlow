export type WorldLineNodeType =
  | 'song'
  | 'artist'
  | 'album'
  | 'genre'
  | 'creator'
  | 'era'
  | 'related_work'

export interface WorldLineEntity {
  entity_id: string
  entity_type: 'song' | 'artist' | 'album'
  name: string
  artist: string | null
  album: string | null
  source: string
  metadata: Record<string, unknown>
}

export interface WorldLineNode {
  id: string
  type: WorldLineNodeType
  name: string
  description: string
  metadata: Record<string, unknown>
}

export interface WorldLineEdge {
  source: string
  target: string
  relation: string
  description: string
  evidence_ids: string[]
}

export interface WorldLineSource {
  id: string
  source_type: 'music_api' | 'knowledge'
  title: string
  source: string
  url: string | null
  excerpt: string | null
  metadata: Record<string, unknown>
}

export interface WorldLineBranch {
  id: string
  title: string
  description: string
  branch_type: string
  target_entity: { id: string; type: WorldLineNodeType; name: string }
}

export interface WorldLineAction {
  id: string
  target_node_id: string
  action_type: 'explore_node'
  enabled: boolean
  disabled_reason: string | null
}

export interface WorldLineSessionResult {
  root_entity: WorldLineEntity
  graph: { root: string; nodes: WorldLineNode[]; edges: WorldLineEdge[] }
  narrative: string
  branches: WorldLineBranch[]
  sources: WorldLineSource[]
  citations: WorldLineSource[]
  session_id: string
  current_node: string
  visited_nodes: string[]
  available_actions: WorldLineAction[]
  exploration_depth: number
  revision: number
  outcome: string | null
}
