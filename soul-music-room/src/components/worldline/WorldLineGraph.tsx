import type {
  WorldLineAction,
  WorldLineEdge,
  WorldLineNode,
  WorldLineSessionResult,
} from '../../types/worldline'
import { buildWorldLineLayout, worldLineEdgeKey } from './worldlineViewModel'

interface WorldLineGraphProps {
  result: WorldLineSessionResult
  selectedEdge: WorldLineEdge | null
  pendingBranchId: string | null
  onExplore: (branchId: string) => void
  onSelectEdge: (edge: WorldLineEdge) => void
}

const WIDTH = 1000
const HEIGHT = 620

function relationLabel(relation: string) {
  return relation.replaceAll('_', ' ')
}

function nodeClass(
  node: WorldLineNode,
  result: WorldLineSessionResult,
  action: WorldLineAction | undefined,
) {
  return [
    'worldline-graph__node',
    `worldline-graph__node--${node.type}`,
    result.current_node === node.id ? 'is-current' : '',
    result.visited_nodes.includes(node.id) ? 'is-visited' : '',
    action?.enabled ? 'is-actionable' : 'is-disabled',
  ].filter(Boolean).join(' ')
}

export default function WorldLineGraph({
  result,
  selectedEdge,
  pendingBranchId,
  onExplore,
  onSelectEdge,
}: WorldLineGraphProps) {
  const positions = buildWorldLineLayout(result.graph.nodes, result.graph.root)
  const actionByNode = new Map(
    result.available_actions.map((action) => [action.target_node_id, action]),
  )
  const selectedKey = selectedEdge ? worldLineEdgeKey(selectedEdge) : null

  return (
    <div className="worldline-graph" data-testid="worldline-graph">
      <svg
        viewBox={`0 0 ${WIDTH} ${HEIGHT}`}
        role="img"
        aria-label={`WorldLine 图谱，共 ${result.graph.nodes.length} 个节点、${result.graph.edges.length} 条关系`}
      >
        <defs>
          <marker id="worldline-arrow" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse">
            <path d="M 0 0 L 10 5 L 0 10 z" />
          </marker>
          <filter id="worldline-glow" x="-80%" y="-80%" width="260%" height="260%">
            <feGaussianBlur stdDeviation="8" result="blur" />
            <feMerge><feMergeNode in="blur" /><feMergeNode in="SourceGraphic" /></feMerge>
          </filter>
        </defs>

        <g className="worldline-graph__edges">
          {result.graph.edges.map((edge, index) => {
            const source = positions.get(edge.source)
            const target = positions.get(edge.target)
            if (!source || !target) return null
            const key = worldLineEdgeKey(edge)
            const curve = (index % 3 - 1) * 22
            const middleX = (source.x + target.x) / 2
            const middleY = (source.y + target.y) / 2 + curve
            const path = `M ${source.x} ${source.y} Q ${middleX} ${middleY} ${target.x} ${target.y}`
            const selected = selectedKey === key
            return (
              <g
                className={`worldline-graph__edge${selected ? ' is-selected' : ''}`}
                key={key}
                role="button"
                tabIndex={0}
                aria-label={`查看 ${edge.source} 到 ${edge.target} 的 ${edge.relation} 证据`}
                onClick={() => onSelectEdge(edge)}
                onKeyDown={(event) => {
                  if (event.key === 'Enter' || event.key === ' ') {
                    event.preventDefault()
                    onSelectEdge(edge)
                  }
                }}
              >
                <path className="worldline-graph__edge-line" d={path} markerEnd="url(#worldline-arrow)" />
                <path className="worldline-graph__edge-hit" d={path} />
                <text x={middleX} y={middleY - 8} textAnchor="middle">
                  {relationLabel(edge.relation)}
                </text>
              </g>
            )
          })}
        </g>

        <g className="worldline-graph__nodes">
          {result.graph.nodes.map((node) => {
            const point = positions.get(node.id)
            if (!point) return null
            const action = actionByNode.get(node.id)
            const actionable = Boolean(action?.enabled)
            const pending = action?.id === pendingBranchId
            const label = actionable ? `探索 ${node.name}` : `${node.name}，当前不可探索`
            return (
              <g
                key={node.id}
                transform={`translate(${point.x} ${point.y})`}
                className={nodeClass(node, result, action)}
                role={actionable ? 'button' : undefined}
                tabIndex={actionable ? 0 : undefined}
                aria-label={label}
                aria-disabled={!actionable}
                data-node-id={node.id}
                data-node-type={node.type}
                onClick={() => action?.enabled && onExplore(action.id)}
                onKeyDown={(event) => {
                  if (action?.enabled && (event.key === 'Enter' || event.key === ' ')) {
                    event.preventDefault()
                    onExplore(action.id)
                  }
                }}
              >
                <title>{`${node.name} · ${node.type} · ${node.id}`}</title>
                <circle className="worldline-graph__node-aura" r="54" />
                <circle className="worldline-graph__node-core" r="42" />
                <text className="worldline-graph__node-type" y="-8" textAnchor="middle">{node.type}</text>
                <text className="worldline-graph__node-name" y="14" textAnchor="middle">
                  {node.name.length > 15 ? `${node.name.slice(0, 14)}…` : node.name}
                </text>
                {result.current_node === node.id && <text className="worldline-graph__node-state" y="68" textAnchor="middle">CURRENT</text>}
                {pending && <text className="worldline-graph__node-state" y="68" textAnchor="middle">LOADING</text>}
              </g>
            )
          })}
        </g>
      </svg>
      <div className="worldline-graph__legend" aria-label="图谱图例">
        <span><i className="is-song" />歌曲</span>
        <span><i className="is-artist" />艺人</span>
        <span><i className="is-album" />专辑</span>
        <span><i className="is-genre" />流派</span>
        <span><i className="is-visited" />已访问</span>
      </div>
    </div>
  )
}
