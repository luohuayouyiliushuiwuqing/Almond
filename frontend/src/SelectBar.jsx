import { useStore, getVisibleRecords, selectVisible, clearSelection, invertSelection, enqueueBackfill, removeSelected, update } from './store.js'
import { openModal } from './ui.js'

export default function SelectBar() {
  const s = useStore()
  const n = s.selectedUrls.size
  if (n === 0) return null
  const noVisible = getVisibleRecords().length === 0

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
      <button className="btn-danger" onClick={removeSelected}>删除</button>
      <span className="sel-sep" />
      <button className="btn-ghost" onClick={() => openModal('import_export')}>导入导出</button>
    </div>
  )
}
