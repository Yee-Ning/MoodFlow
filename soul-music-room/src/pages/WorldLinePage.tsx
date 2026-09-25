import { useEffect, useMemo, useState, type FormEvent } from 'react'
import { Compass, LoaderCircle, Plus, RotateCcw } from 'lucide-react'
import { useLocation } from 'react-router-dom'
import WorldLineEvidencePanel from '../components/worldline/WorldLineEvidencePanel'
import WorldLineGraph from '../components/worldline/WorldLineGraph'
import { worldLineEdgeKey } from '../components/worldline/worldlineViewModel'
import { useWorldLineStore } from '../store/worldlineStore'
import type { WorldLineEdge } from '../types/worldline'
import './WorldLinePage.css'

const disabledReason: Record<string, string> = {
  already_visited: '已探索',
  depth_limit_reached: '已达深度上限',
  genre_not_executable: '当前流派不可执行',
  genre_not_expandable: '当前流派不可扩展',
  insufficient_evidence: '证据不足',
  unsupported_target_type: '当前版本暂不支持',
  invalid_branch_target: '目标已失效',
}

function WorldLinePage() {
  const location = useLocation()
  const initialQuery = (location.state as { query?: string } | null)?.query ?? ''
  const [query, setQuery] = useState(initialQuery)
  const [selectedEdge, setSelectedEdge] = useState<WorldLineEdge | null>(null)
  const {
    result,
    sessionId,
    status,
    operation,
    loading,
    pendingBranchId,
    error,
    createSession,
    recoverSession,
    explore,
    clearSession,
  } = useWorldLineStore()

  useEffect(() => {
    void recoverSession()
  }, [recoverSession])

  const activeEdge = useMemo(() => {
    if (!result || !selectedEdge) return null
    const key = worldLineEdgeKey(selectedEdge)
    return result.graph.edges.find((edge) => worldLineEdgeKey(edge) === key) ?? null
  }, [result, selectedEdge])

  const submit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    setSelectedEdge(null)
    void createSession(query)
  }

  const startOver = () => {
    clearSession()
    setSelectedEdge(null)
    setQuery('')
  }

  const executableActions = result?.available_actions.filter((action) => action.enabled) ?? []
  const creating = operation === 'create'
  const recovering = operation === 'recover'

  return (
    <main className="worldline-page">
      <div className="worldline-page__ambient" aria-hidden="true" />
      <header className="worldline-page__header">
        <div>
          <p className="worldline-eyebrow">MUSIC WORLDLINE / V2.4B</p>
          <h1>沿证据，继续探索音乐关系</h1>
          <p className="worldline-page__lede">从一首歌、一位艺人或一张专辑出发。每次扩展都由服务端验证，并保留在同一条 WorldLine 会话中。</p>
        </div>
        {result && (
          <button className="worldline-page__reset" type="button" onClick={startOver} disabled={loading}>
            <Plus size={15} aria-hidden="true" /> 新建世界线
          </button>
        )}
      </header>

      {!result && (
        <section className="worldline-page__entry" aria-labelledby="worldline-entry-title">
          <div className="worldline-page__entry-mark" aria-hidden="true"><Compass size={32} /></div>
          <div>
            <p className="worldline-eyebrow">START A SESSION</p>
            <h2 id="worldline-entry-title">输入你的音乐起点</h2>
          </div>
          <form onSubmit={submit}>
            <label htmlFor="worldline-query">歌曲、歌手或专辑</label>
            <div>
              <input
                id="worldline-query"
                value={query}
                onChange={(event) => setQuery(event.target.value)}
                placeholder="例如：Nujabes - Feather"
                maxLength={4000}
                disabled={recovering}
              />
              <button type="submit" disabled={loading || !query.trim()}>
                {creating && <LoaderCircle className="worldline-spin" size={16} aria-hidden="true" />}
                {creating ? '正在创建' : '创建世界线'}
              </button>
            </div>
          </form>
          {sessionId && status !== 'expired' && (
            <div className="worldline-page__recovery" role="status">
              {recovering ? (
                <><LoaderCircle className="worldline-spin" size={17} aria-hidden="true" /> 正在从 checkpoint 恢复会话…</>
              ) : (
                <><span>会话恢复未完成。</span><button type="button" onClick={() => void recoverSession()}>重试恢复</button></>
              )}
            </div>
          )}
        </section>
      )}

      {error && (
        <div className="worldline-page__error" role="alert">
          <span>{error}</span>
          {result && <small>当前图谱已保留，没有写入失败的探索结果。</small>}
          {status === 'expired' && <button type="button" onClick={startOver}>创建新会话</button>}
        </div>
      )}

      {result && (
        <>
          <section className="worldline-page__session" aria-label="WorldLine 会话状态">
            <div><span>ROOT</span><strong>{result.root_entity.name}</strong></div>
            <div><span>DEPTH</span><strong>{result.exploration_depth}</strong></div>
            <div><span>VISITED</span><strong>{result.visited_nodes.length}</strong></div>
            <div><span>REVISION</span><strong>{result.revision}</strong></div>
            <code title={result.session_id}>{result.session_id.slice(0, 12)}</code>
          </section>

          <div className="worldline-page__workspace">
            <section className="worldline-page__graph-panel" aria-labelledby="worldline-graph-title" aria-busy={operation === 'explore'}>
              <div className="worldline-page__section-heading">
                <div>
                  <p className="worldline-eyebrow">GRAPH</p>
                  <h2 id="worldline-graph-title">关系图谱</h2>
                </div>
                <span>{result.graph.nodes.length} nodes · {result.graph.edges.length} edges</span>
              </div>
              {result.graph.nodes.length === 0 ? (
                <div className="worldline-page__empty">当前会话还没有可展示的图谱节点。</div>
              ) : (
                <WorldLineGraph
                  result={result}
                  selectedEdge={activeEdge}
                  pendingBranchId={pendingBranchId}
                  onExplore={(branchId) => void explore(branchId)}
                  onSelectEdge={setSelectedEdge}
                />
              )}
              {operation === 'explore' && (
                <div className="worldline-page__graph-loading" role="status">
                  <LoaderCircle className="worldline-spin" size={18} aria-hidden="true" />
                  正在验证证据并扩展图谱…
                </div>
              )}
            </section>

            <WorldLineEvidencePanel
              edge={activeEdge}
              nodes={result.graph.nodes}
              sources={result.sources}
              citations={result.citations}
              eraRelations={result.era_relations}
              sceneRelations={result.scene_relations}
              onClose={() => setSelectedEdge(null)}
            />
          </div>

          <div className="worldline-page__lower-grid">
            <section className="worldline-page__narrative" aria-labelledby="worldline-narrative-title">
              <div className="worldline-page__section-heading">
                <div><p className="worldline-eyebrow">NARRATIVE</p><h2 id="worldline-narrative-title">当前叙事</h2></div>
              </div>
              <p>{result.narrative}</p>
              {result.outcome === 'no_new_connections' && <small>本轮没有发现新的已验证关系，原有图谱保持不变。</small>}
            </section>

            <section className="worldline-page__branches" aria-labelledby="worldline-branches-title">
              <div className="worldline-page__section-heading">
                <div><p className="worldline-eyebrow">NEXT</p><h2 id="worldline-branches-title">继续探索</h2></div>
                <span>{executableActions.length} available</span>
              </div>
              {result.branches.length === 0 ? (
                <div className="worldline-page__empty">当前没有更多可探索分支。</div>
              ) : (
                <ul>
                  {result.branches.map((branch) => {
                    const action = result.available_actions.find((item) => item.id === branch.id)
                    const pending = pendingBranchId === branch.id
                    const reason = action?.disabled_reason
                    return (
                      <li key={branch.id}>
                        <button
                          type="button"
                          disabled={loading || !action?.enabled}
                          onClick={() => action?.enabled && void explore(action.id)}
                        >
                          <span className={`worldline-page__branch-type is-${branch.target_entity.type}`}>{branch.target_entity.type}</span>
                          <span><strong>{branch.title}</strong><small>{branch.description}</small></span>
                          {pending ? <LoaderCircle className="worldline-spin" size={16} aria-hidden="true" /> : <span aria-hidden="true">↗</span>}
                        </button>
                        {!action?.enabled && <small>{disabledReason[reason ?? ''] ?? '当前不可执行'}</small>}
                      </li>
                    )
                  })}
                </ul>
              )}
              {result.branches.length > 0 && executableActions.length === 0 && (
                <p className="worldline-page__no-actions">这些关系仍可查看，但当前没有可执行探索动作。</p>
              )}
            </section>
          </div>

          <footer className="worldline-page__footer">
            <span>{result.sources.length} evidence sources</span>
            <span>{result.citations.length} narrative citations</span>
            <span className="worldline-page__checkpoint" title="当前数据已由服务端恢复">
              <RotateCcw size={13} aria-hidden="true" /> checkpoint synced
            </span>
          </footer>
        </>
      )}
    </main>
  )
}

export default WorldLinePage
