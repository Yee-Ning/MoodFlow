import { ExternalLink, X } from 'lucide-react'
import type {
  EraRelation,
  SceneRelation,
  WorldLineEdge,
  WorldLineNode,
  WorldLineSource,
} from '../../types/worldline'
import { resolveEdgeEvidence } from './worldlineViewModel'

interface WorldLineEvidencePanelProps {
  edge: WorldLineEdge | null
  nodes: WorldLineNode[]
  sources: WorldLineSource[]
  citations: WorldLineSource[]
  eraRelations?: EraRelation[]
  sceneRelations?: SceneRelation[]
  onClose: () => void
}

function metadataEntries(metadata: Record<string, unknown>) {
  return Object.entries(metadata).filter(([, value]) => (
    typeof value === 'string' || typeof value === 'number' || typeof value === 'boolean'
  )).slice(0, 6)
}

function nodeName(nodes: WorldLineNode[], id: string) {
  return nodes.find((node) => node.id === id)?.name ?? id
}

export default function WorldLineEvidencePanel({
  edge,
  nodes,
  sources,
  citations,
  eraRelations = [],
  sceneRelations = [],
  onClose,
}: WorldLineEvidencePanelProps) {
  if (!edge) {
    return (
      <aside className="worldline-evidence worldline-evidence--empty" aria-label="关系证据">
        <p className="worldline-eyebrow">EVIDENCE</p>
        <h2>选择一条关系</h2>
        <p>点击图中的连线，查看后端返回的来源、原文片段与引用状态。</p>
      </aside>
    )
  }

  const resolved = resolveEdgeEvidence(edge, sources, citations)
  const eraRelation = eraRelations.find((relation) => (
    relation.source_entity.node_id === edge.source
    && (relation.target_entity?.node_id ?? relation.era?.node_id) === edge.target
    && relation.relation_type === edge.relation
  ))
  const sceneRelation = sceneRelations.find((relation) => (
    relation.source_entity.node_id === edge.source
    && (relation.target_entity?.node_id ?? relation.scene?.node_id) === edge.target
    && relation.relation_type === edge.relation
  ))
  const temporalRelation = eraRelation ?? sceneRelation
  const timeLabel = temporalRelation
    ? temporalRelation.start_year === null && temporalRelation.end_year === null
      ? '时间未确定'
      : temporalRelation.start_year === temporalRelation.end_year
        ? String(temporalRelation.start_year)
        : `${temporalRelation.start_year ?? '?'}–${temporalRelation.end_year ?? '?'}`
    : null
  return (
    <aside className="worldline-evidence" aria-label="关系证据" aria-live="polite">
      <header className="worldline-evidence__header">
        <div>
          <p className="worldline-eyebrow">EVIDENCE</p>
          <h2>{nodeName(nodes, edge.source)} → {nodeName(nodes, edge.target)}</h2>
        </div>
        <button type="button" onClick={onClose} aria-label="关闭证据面板">
          <X size={18} aria-hidden="true" />
        </button>
      </header>
      <dl className="worldline-evidence__relation">
        <div><dt>Relation</dt><dd>{edge.relation}</dd></div>
        <div><dt>Evidence IDs</dt><dd>{edge.evidence_ids.length ? edge.evidence_ids.join(', ') : '无'}</dd></div>
        {temporalRelation && <>
          {sceneRelation && <>
            <div><dt>Scene</dt><dd>{sceneRelation.scene_name}</dd></div>
            <div><dt>Location</dt><dd>{sceneRelation.location ?? '未确认'}</dd></div>
          </>}
          <div><dt>Period</dt><dd>{timeLabel} · {temporalRelation.temporal_precision}</dd></div>
          <div><dt>Confidence</dt><dd>{temporalRelation.confidence.toFixed(2)}</dd></div>
          {temporalRelation.metadata.temporal_conflict === true && (
            <div><dt>Time conflict</dt><dd>来源年份不一致，精确年份未确定。请查看各条证据。</dd></div>
          )}
          {sceneRelation?.metadata.location_conflict === true && (
            <div><dt>Location conflict</dt><dd>来源地点不一致，地点未确定。请查看各条证据。</dd></div>
          )}
        </>}
      </dl>
      {resolved.length === 0 ? (
        <div className="worldline-evidence__unavailable" role="status">
          <strong>证据暂不可用</strong>
          <p>该关系没有可映射的后端 evidence source。页面不会自行补写证据。</p>
        </div>
      ) : (
        <div className="worldline-evidence__sources">
          {resolved.map(({ source, cited }) => (
            <article key={source.id} className="worldline-evidence__source">
              <div className="worldline-evidence__source-heading">
                <div>
                  <span>{source.source_type}</span>
                  {cited && <span className="is-cited">narrative citation</span>}
                </div>
                <strong>{source.title}</strong>
                <small>{source.source}</small>
              </div>
              {source.excerpt ? (
                <blockquote>{source.excerpt}</blockquote>
              ) : (
                <p className="worldline-evidence__missing">后端未提供 exact excerpt / span。</p>
              )}
              {metadataEntries(source.metadata).length > 0 && (
                <dl className="worldline-evidence__metadata">
                  {metadataEntries(source.metadata).map(([key, value]) => (
                    <div key={key}><dt>{key}</dt><dd>{String(value)}</dd></div>
                  ))}
                </dl>
              )}
              {source.url && (
                <a href={source.url} target="_blank" rel="noreferrer">
                  查看来源 <ExternalLink size={13} aria-hidden="true" />
                </a>
              )}
              <code>{source.id}</code>
            </article>
          ))}
        </div>
      )}
    </aside>
  )
}
