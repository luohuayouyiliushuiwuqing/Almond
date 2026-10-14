import { useEffect, useState } from 'react'
import { useStore, fsState, loadStorageDir, loadFs, dropHistory, saveDbPath } from './store.js'
import { closeModal } from './ui.js'

function fsJoin(dir, name) {
  if (!dir) return name
  const sep = dir.includes('\\') ? '\\' : '/'
  return /[\\/]$/.test(dir) ? dir + name : dir + sep + name
}

export default function StoragePanel() {
  const [dir, setDir] = useState(null)
  const [history, setHistory] = useState([])
  const [input, setInput] = useState('')
  const [fs, setFs] = useState({ path: '', parent: null, entries: [] })
  const [pickerOpen, setPickerOpen] = useState(false)
  const [saving, setSaving] = useState(false)

  const refresh = async () => {
    const d = await loadStorageDir()
    setDir(d)
    setHistory(d.history || [])
  }
  useEffect(() => { refresh() }, [])

  const openFs = async (path) => {
    const d = await loadFs(path)
    if (d && d.ok) {
      setFs(d)
      setPickerOpen(true)
    }
  }

  const onSave = async () => {
    const path = input.trim()
    if (!path) return
    setSaving(true)
    try { await saveDbPath(path) } finally { setSaving(false); refresh() }
  }
  const onHist = async (p) => {
    setInput(p)
    await saveDbPath(p)
    refresh()
  }
  const onDel = async (p) => {
    const d = await dropHistory(p)
    setHistory(d.history || [])
  }
  const onPick = () => {
    if (fs.path) { setInput(fs.path); setPickerOpen(false) }
  }

  const isCur = (p) => dir && p === dir.path

  return (
    <div className="modal-mask" onClick={(e) => { if (e.target === e.currentTarget) closeModal() }}>
      <div className="modal db-modal">
        <div className="modal-title">存储目录</div>

        <div className={'db-current' + (dir && dir.exists === false ? ' missing' : '')}>
          {dir
            ? `目录: ${dir.path}\n数据库: ${dir.db_path}\n图片缓存: ${dir.cache_dir}`
            : '加载中…'}
        </div>

        <div className="db-history">
          {history.length ? history.map((p) => (
            <div className={'db-hist-row' + (isCur(p) ? ' current' : '')} key={p}>
              <button className="db-hist-item" disabled={isCur(p)} onClick={() => onHist(p)}>{p}</button>
              {isCur(p)
                ? <span className="db-hist-cur">当前</span>
                : <button className="db-hist-del" onClick={() => onDel(p)}>移除</button>}
            </div>
          )) : <div className="fs-empty">暂无历史记录</div>}
        </div>

        <div className="proxy-section" style={{ marginTop: 12 }}>
          <label>选择或输入目录</label>
          <div className="proxy-row">
            <input
              className="input grow"
              style={{ flex: 1 }}
              placeholder="目录路径，如 D:/almond-data"
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => { if (e.key === 'Enter') onSave() }}
            />
            <button className="btn-ghost" onClick={() => openFs(fs.path || (dir && dir.path) || '')}>浏览</button>
          </div>
        </div>

        {pickerOpen && (
          <div className="fs-picker">
            <div className="fs-nav">
              <button className="btn-ghost" disabled={!fs.parent} onClick={() => openFs(fs.parent)}>↑ 上级</button>
              <span className="fs-path">{fs.path}</span>
            </div>
            <div className="fs-list">
              {fs.entries && fs.entries.length ? (
                fs.entries.map((name) => (
                  <button className="fs-item" key={name} onClick={() => openFs(fsJoin(fs.path, name))}>{name}</button>
                ))
              ) : (
                <div className="fs-empty">（无子目录）</div>
              )}
            </div>
            <div className="fs-actions">
              <span className="fs-hint">点子目录进入，选好后点「选这个」</span>
              <button className="btn-primary" onClick={onPick}>选这个</button>
            </div>
          </div>
        )}

        <div className="modal-actions">
          <button className="btn-ghost" onClick={closeModal}>关闭</button>
          <button className="btn-primary" disabled={saving} onClick={onSave}>保存并切换</button>
        </div>
      </div>
    </div>
  )
}
