import type {
  WorldLineEdge,
  WorldLineNode,
  WorldLineSource,
} from '../../types/worldline'

export interface WorldLinePoint {
  x: number
  y: number
}

export interface ResolvedEdgeEvidence {
  source: WorldLineSource
  cited: boolean
}

const CENTER: WorldLinePoint = { x: 500, y: 310 }

export function worldLineEdgeKey(edge: WorldLineEdge) {
  return `${edge.source}\u0000${edge.target}\u0000${edge.relation}`
}

export function buildWorldLineLayout(nodes: WorldLineNode[], rootId: string) {
  const positions = new Map<string, WorldLinePoint>()
  const root = nodes.find((node) => node.id === rootId)
  if (root) positions.set(root.id, CENTER)
  const orbit = nodes.filter((node) => node.id !== rootId)
  orbit.forEach((node, index) => {
    const angle = -Math.PI / 2 + (Math.PI * 2 * index) / Math.max(orbit.length, 1)
    const ring = orbit.length > 9 && index % 2 === 1 ? 0.72 : 1
    positions.set(node.id, {
      x: CENTER.x + Math.cos(angle) * 365 * ring,
      y: CENTER.y + Math.sin(angle) * 225 * ring,
    })
  })
  return positions
}

export function resolveEdgeEvidence(
  edge: WorldLineEdge,
  sources: WorldLineSource[],
  citations: WorldLineSource[],
): ResolvedEdgeEvidence[] {
  const sourceById = new Map(sources.map((source) => [source.id, source]))
  const citationIds = new Set(citations.map((citation) => citation.id))
  return edge.evidence_ids.flatMap((evidenceId) => {
    const source = sourceById.get(evidenceId)
    return source ? [{ source, cited: citationIds.has(source.id) }] : []
  })
}
