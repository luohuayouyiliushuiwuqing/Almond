import { useEffect, useState } from 'react'
import { useStore, tagRecord, untagRecord, loadRecords } from './store.js'
import { apiGet, apiPost } from './api.js'
import { closeModal } from './ui.js'

/* payload: 单条记录（record）或多选 { urls: [...] }，共用同一弹窗 */
export default function TagModal({ payload }) {
  const s = useStore()
  const urls = payload.urls ? payload.urls : [payload.url]
  const [tags, setTags] = useState([])
  const [val, setVal] = useState('')

  const recs = urls.map((u) => s.records.find((r) => r.url === u)).filter(Boolean)

  const load = async () => {
    try {
      const data = await apiGet('/api/tags')
      setTags(data.tags || [])
    } catch (e) { setTags([]) }
  }
  useEffect(() => { load() }, [])

  /* 目标里每一条都带该标签 → 视为已选（✓），否则 +（点一下补齐） */
  const hasAll = (id) => recs.length > 0 && recs.every((r) => (r.tags || []).some((t) => t.id === id))

  const toggle = async (tag) => {
    const on = hasAll(tag.id)
    for (const rec of recs) {
      if (on) await untagRecord(rec.url, tag.id)
      else await tagRecord(rec.url, tag.id)
    }
    await loadRecords()
    await load()
  }

  const addNew = async () => {
    const name = val.trim()
    if (!name) return
    setVal('')
    await apiPost('/api/tag', { name })  // 见底部 import
    await load()
  }

  return (
    <div className="modal-mask" onClick={(e) => { if (e.target === e.currentTarget) closeModal() }}>
      <div className="modal">
        <div className="modal-title">标签</div>
        <div className="detail-domain" style={{ whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
          {urls.length > 1 ? `批量操作：${urls.length} 条记录` : urls[0]}
        </div>

        <div className="tag-list">
          {tags.map((tag) => {
            const on = hasAll(tag.id)
            return (
              <div
                className={'tag-item' + (on ? ' active' : '')}
                key={tag.id}
                onClick={() => toggle(tag)}
              >
                <span>{tag.name}</span>
                <span className="tag-toggle">{on ? '✓' : '+'}</span>
              </div>
            )
          })}
          {!tags.length && <div className="rule-empty">还没有标签</div>}
        </div>

        <div className="tag-add-row">
          <input
            className="input"
            placeholder="新建标签…"
            value={val}
            onChange={(e) => setVal(e.target.value)}
            onKeyDown={(e) => { if (e.key === 'Enter') addNew() }}
          />
          <button className="btn-ghost" onClick={addNew}>添加</button>
        </div>

        <div className="modal-actions">
          <button className="btn-primary" onClick={closeModal}>完成</button>
        </div>
      </div>
    </div>
  )
}
