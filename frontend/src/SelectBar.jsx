import { useStore, getVisibleRecords, selectVisible, clearSelection, invertSelection, enqueueBackfill, removeRecord, update } from './store.js'
import { openModal } from './ui.js'

export default function SelectBar() {
  const s = useStore()
  const n = s.selectedUrls.size
  if (n === 0) return null
  const noVisible = getVisibleRecords().length === 0

  const exportSelected = (format) => {
    const known = new Set(s.records.map((r) => r.url))
    const recs = [...s.selectedUrls].filter((u) => known.has(u)).map((u) => s.records.find((r) => r.url === u))
    const lines = recs.map((r) => r.url).join('\n') + '\n'
    const blob = new Blob([lines], { type: 'text/plain;charset=utf-8' })
    const a = document.createElement('a')
    a.href = URL.createObjectURL(blob)
    a.download = 'bookmarks.txt'
    a.click()
    URL.revokeObjectURL(a.href)
  }

  const deleteSelected = async () => {
    const urls = [...s.selectedUrls]
    if (!urls.length) return
    if (!window.confirm(`确定删除选中的 ${urls.length} 条记录？`)) return
    let ok = 0
    let fail = 0
    for (const u of urls) {
      try {
        const res = await fetch(`/api/record?url=${encodeURIComponent(u)}`, { method: 'DELETE' })
        const data = await res.json()
        if (data.ok) {
          s.records = s.records.filter((r) => r.url !== u)
          s.selectedUrls.delete(u)
          ok++
        } else fail++
      } catch {
        fail++
      }
    }
    // 触发重渲
    update({ records: s.records.slice(), selectedUrls: new Set(s.selectedUrls) })
  }

  const refetchSelected = () => {
    const known = new Set(s.records.map((r) => r.url))
    const urls = [...s.selectedUrls].filter((u) => known.has(u))
    enqueueBackfill(urls, `批量重新抓取 ${urls.length} 条`, true)
    s.selectedUrls.clear()
    update({ selectedUrls: new Set() })
  }

  return (
    <div className="sel-bar">
      <span className="sel-count">已选 {n} 条</span>
      <button className="btn-ghost" disabled={noVisible} onClick={selectVisible}>全选</button>
      <button className="btn-ghost" onClick={clearSelection}>全不选</button>
      <button className="btn-ghost" disabled={noVisible} onClick={invertSelection}>反选</button>
      <span className="sel-sep" />
      <button className="btn-ghost" onClick={() => openModal('tag', { urls: [...s.selectedUrls] })}>标签</button>
      <button className="btn-ghost" onClick={refetchSelected}>重新抓取</button>
      <button className="btn-danger" onClick={deleteSelected}>删除</button>
      <span className="sel-sep" />
      <select className="input export-select" defaultValue="" onChange={(e) => { const f = e.target.value; e.target.value = ''; if (f) exportSelected(f) }}>
        <option value="">导出</option>
        <option value="txt">TXT</option>
      </select>
    </div>
  )
}
