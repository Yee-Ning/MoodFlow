import { ExternalLink, X } from 'lucide-react'
import type {
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
