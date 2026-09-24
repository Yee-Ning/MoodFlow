import { useState, type FormEvent } from 'react'
import { useLocation } from 'react-router-dom'
import { useWorldLineStore } from '../store/worldlineStore'
import './WorldLinePage.css'

function WorldLinePage() {
  const location = useLocation()
  const initialQuery = (location.state as { query?: string } | null)?.query ?? ''
  const [query, setQuery] = useState(initialQuery)
  const { result, loading, error, createSession, explore } = useWorldLineStore()

  const submit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    void createSession(query)
  }

  return (
    <main className="worldline-page">
      <header className="worldline-page__header">
        <p>MUSIC WORLDLINE</p>
        <h1>沿音乐关系继续探索</h1>
        <form onSubmit={submit}>
          <label htmlFor="worldline-query">歌曲、歌手或专辑</label>
          <div>
            <input id="worldline-query" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="例如：Nujabes - Feather" maxLength={4000} />
            <button type="submit" disabled={loading || !query.trim()}>{loading ? '探索中…' : '创建世界线'}</button>
          </div>
        </form>
        {error && <p className="worldline-page__error" role="alert">{error}</p>}
      </header>
      {result && (
        <div className="worldline-page__content">
          <section aria-label="当前世界线">
            <p>起点：{result.root_entity.name} · 已探索 {result.exploration_depth} 步</p>
            <h2>关系节点</h2>
            <ul className="worldline-page__nodes">
              {result.graph.nodes.map((node) => {
                const action = result.available_actions.find((item) => item.target_node_id === node.id)
                return <li key={node.id}>
                  <button type="button" disabled={loading || !action?.enabled} onClick={() => action && void explore(action.id)} aria-label={`探索 ${node.name}`}>
                    <span>{node.type}</span><strong>{node.name}</strong>
                    {node.id === result.current_node && <em>当前节点</em>}
                  </button>
                </li>
              })}
            </ul>
            <p>{result.graph.edges.length} 条关系 · {result.sources.length} 个证据来源</p>
          </section>
          <section aria-label="探索叙事">
            <h2>本轮叙事</h2>
            <p className="worldline-page__narrative">{result.narrative}</p>
            {result.outcome === 'no_new_connections' && <p>这次探索未发现新的目录关系，已保留原有世界线。</p>}
            <h2>继续探索</h2>
            {result.branches.length === 0 ? <p>当前没有更多可探索的节点。</p> : (
              <ul className="worldline-page__branches">
                {result.branches.map((branch) => {
                  const action = result.available_actions.find((item) => item.id === branch.id)
                  return <li key={branch.id}>
                    <button type="button" disabled={loading || !action?.enabled} onClick={() => void explore(branch.id)}>
                      <strong>{branch.title}</strong><span>{branch.description}</span>
                    </button>
                    {action?.disabled_reason === 'unsupported_target_type' && <small>当前版本暂不支持该类型扩展</small>}
                  </li>
                })}
              </ul>
            )}
            {result.citations.length > 0 && <details><summary>引用来源</summary><ul>{result.citations.map((citation) => <li key={citation.id}>{citation.title}</li>)}</ul></details>}
          </section>
        </div>
      )}
    </main>
  )
}

export default WorldLinePage
