export interface WorldLineEntity {
  entity_id: string
  entity_type: 'song' | 'artist' | 'album'
  name: string
}

export interface WorldLineNode {
  id: string
  type: string
  name: string
  description: string
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
  title: string
  source: string
  url: string | null
}

export interface WorldLineBranch {
  id: string
  title: string
  description: string
  branch_type: string
  target_entity: { id: string; type: string; name: string }
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
  available_actions: WorldLineAction[]
  exploration_depth: number
  revision: number
  outcome: string | null
}
