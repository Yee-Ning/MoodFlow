export type WorldLineNodeType =
  | 'song'
  | 'artist'
  | 'album'
  | 'genre'
  | 'creator'
  | 'era'
  | 'scene'
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

export type EraRelationType =
  | 'belongs_to_era'
  | 'representative_of_era'
  | 'temporal_association'
  | 'career_phase'
  | 'style_era_association'

export interface EraEntityReference {
  node_id: string
  node_type: WorldLineNodeType
  canonical_name: string
  knowledge_entity_id: string | null
}

export interface EraRelationEvidence {
  evidence_id: string
  source_id: string
  claim_id: string
  exact_span: string
  start_year: number | null
  end_year: number | null
  raw_time: string | null
  confidence: number
}

export interface EraRelation {
  source_entity: EraEntityReference
  target_entity: EraEntityReference | null
  era: { node_id: string; canonical_name: string } | null
  relation_type: EraRelationType
  start_year: number | null
  end_year: number | null
  temporal_precision: 'exact' | 'approximate' | 'unknown'
  confidence: number
  evidence: EraRelationEvidence[]
  metadata: Record<string, unknown>
}

export type SceneRelationType =
  | 'belongs_to_scene'
  | 'representative_of_scene'
  | 'scene_association'
  | 'style_scene_association'
  | 'emerged_from_scene'

export interface SceneRelation {
  source_entity: EraEntityReference
  target_entity: EraEntityReference | null
  scene: { node_id: string; canonical_name: string } | null
  relation_type: SceneRelationType
  scene_name: string
  location: string | null
  start_year: number | null
  end_year: number | null
  temporal_precision: 'exact' | 'approximate' | 'unknown'
  confidence: number
  evidence: EraRelationEvidence[]
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
  era_relations: EraRelation[]
  scene_relations: SceneRelation[]
  session_id: string
  current_node: string
  visited_nodes: string[]
  available_actions: WorldLineAction[]
  exploration_depth: number
  revision: number
  outcome: string | null
}
