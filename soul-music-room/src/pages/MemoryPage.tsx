import { useCallback, useEffect, useState, type FormEvent } from 'react'
import { Brain, LoaderCircle } from 'lucide-react'
import { useNavigate } from 'react-router-dom'
import { useAuthStore } from '../store/authStore'
import { correctMemory, forgetMemory, listMemories, setMemoryEnabled, type MemoryRecord } from '../services/memory'
import './MemoryPage.css'

function MemoryPage() {
  const loggedIn = useAuthStore((state) => state.loggedIn)
  const initialized = useAuthStore((state) => state.initialized)
  const navigate = useNavigate()
  const [rows, setRows] = useState<MemoryRecord[]>([])
  const [cursor, setCursor] = useState<string | null>(null)
  const [enabled, setEnabled] = useState(true)
  const [loading, setLoading] = useState(false)
  const [busy, setBusy] = useState('')
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [editing, setEditing] = useState('')
  const [text, setText] = useState('')
  const [editRequestId, setEditRequestId] = useState('')
  const [confirming, setConfirming] = useState('')
  const load = useCallback(async (after = '', signal?: AbortSignal) => {
    setLoading(true)
    try {
      const page = await listMemories(after, signal)
      setRows((previous) => after ? [...previous, ...page.items] : page.items)
      setCursor(page.next_cursor)
      setEnabled(page.preferences.enabled)
      setError('')
    } catch (problem) {
      if (!(problem instanceof DOMException && problem.name === 'AbortError')) {
        setError(problem instanceof Error ? problem.message : '无法读取记忆')
      }
    } finally { if (!signal?.aborted) setLoading(false) }
  }, [])
  useEffect(() => {
    if (!initialized || !loggedIn) return
    const controller = new AbortController()
    queueMicrotask(() => { if (!controller.signal.aborted) void load('', controller.signal) })
    return () => controller.abort()
  }, [initialized, loggedIn, load])

  const act = async (key: string, operation: () => Promise<{ projection_pending?: boolean }>) => {
    setBusy(key); setError(''); setNotice('')
    try {
      const result = await operation()
      setNotice(result.projection_pending ? '操作已保存，个性化信息仍在同步，请稍后刷新。' : '已保存')
      setEditing(''); setConfirming('')
      await load()
    } catch (problem) { setError(problem instanceof Error ? problem.message : '操作未完成，请重试') }
    finally { setBusy('') }
  }
  const submit = (event: FormEvent) => {
    event.preventDefault()
    if (text.trim()) void act(editing, () => correctMemory(editing, text.trim(), editRequestId))
  }
  const active = rows.filter((row) => row.status === 'active')
  const sceneNames: Record<string, string> = { commute: '通勤', workout: '健身', sleep: '睡眠', study: '学习' }
  return (
    <section className="memory-page" aria-labelledby="memory-title">
      <div className="memory-page__heading"><Brain aria-hidden="true" size={30} />
        <div><h1 id="memory-title">我的记忆</h1><p>查看和调整 MoodFlow 用来了解你的偏好与经历。</p></div>
      </div>
      {!initialized ? <p role="status">正在确认登录状态…</p> : !loggedIn ?
        <div className="memory-page__card"><p>登录后可以管理你的记忆。</p>
          <button onClick={() => navigate('/login')}>前往登录</button></div> : <>
        <div className="memory-page__card memory-page__controls">
          <div><h2>个性化记忆</h2><p>暂停后不会读取或新增长期记忆；已保存条目仍可管理。</p></div>
          <label><input type="checkbox" checked={enabled} disabled={Boolean(busy) || loading}
            onChange={(event) => { const next = event.target.checked; void act('preferences', () => setMemoryEnabled(next)) }} />
            {enabled ? '已开启' : '已暂停'}</label>
        </div>
        <p className="memory-page__policy">忘记会删除已保存的记忆和关联证据。原对话与历史记录按既有保留规则保留，已忘记条目不再用于回答。</p>
        {error && <div role="alert" className="memory-page__error">{error}<button disabled={loading} onClick={() => void load()}>重新加载</button></div>}
        {notice && <p role="status">{notice}</p>}
        {loading && <p role="status"><LoaderCircle size={16} /> 正在读取…</p>}
        {!loading && active.length === 0 && !error && <p>当前页没有有效记忆。{cursor ? '可以继续加载其他条目。' : '明确告诉我你的长期偏好后，会显示在这里。'}</p>}
        <div className="memory-page__list">{active.map((row) => <article className="memory-page__card" key={row.memory_id}>
          <div className="memory-page__meta"><span>{row.memory_type === 'profile' ? '偏好' : '经历'}</span>
            {row.structured_content.scene && <span>场景：{sceneNames[row.structured_content.scene] ?? row.structured_content.scene}</span>}
            {row.valid_until && <span>有效期：{new Date(row.valid_until).toLocaleDateString()}</span>}</div>
          <p className="memory-page__content">{row.content}</p>
          {row.evidence_quote && <details><summary>查看依据</summary><blockquote>{row.evidence_quote}</blockquote></details>}
          {editing === row.memory_id ? <form onSubmit={submit} className="memory-page__edit">
            <label htmlFor="memory-correction">用第一人称更正同一主题，例如“我不喜欢周杰伦”</label>
            <textarea id="memory-correction" value={text} maxLength={4000} required onChange={(event) => {
              setText(event.target.value); setEditRequestId(crypto.randomUUID())
            }} />
            <button disabled={Boolean(busy) || !text.trim()} type="submit">保存更正</button>
            <button disabled={Boolean(busy)} type="button" onClick={() => setEditing('')}>取消</button>
          </form> : <div className="memory-page__actions">
            <button disabled={Boolean(busy) || !enabled} onClick={() => {
              setEditing(row.memory_id); setText(''); setEditRequestId(crypto.randomUUID()); setConfirming('')
            }}>更正</button>
            <button disabled={Boolean(busy)} onClick={() => { setConfirming(row.memory_id); setEditing('') }}>忘记</button>
          </div>}
          {confirming === row.memory_id && <div className="memory-page__confirm">
            <p>确认忘记这条记忆及同主题的旧版本？</p>
            <button disabled={Boolean(busy)} onClick={() => void act(row.memory_id, () => forgetMemory(row.memory_id))}>确认忘记</button>
            <button disabled={Boolean(busy)} onClick={() => setConfirming('')}>保留</button>
          </div>}
        </article>)}</div>
        {cursor && <button disabled={loading || Boolean(busy)} onClick={() => void load(cursor)}>加载更多</button>}
      </>}
    </section>
  )
}
export default MemoryPage
