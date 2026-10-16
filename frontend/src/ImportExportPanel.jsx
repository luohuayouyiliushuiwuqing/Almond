import { useRef, useState } from 'react'
import {
  useStore, getVisibleRecords, setStatus, loadRecords, enqueueBackfill,
} from './store.js'
import { closeModal } from './ui.js'
import { apiPost } from './api.js'
import { downloadFile, exportBookmarks, parseImportFile } from './bookmarkIO.js'

/* 导入 / 导出：不常用功能，收进二级页面（模态），不在工具栏常驻。
   对齐 Raindrop 规则（HTML / CSV / TXT），逻辑见 bookmarkIO.js。 */
export default function ImportExportPanel() {
  const s = useStore()
  const fileRef = useRef(null)
  const [scope, setScope] = useState('visible') // 'visible' | 'selected'
  const [format, setFormat] = useState('html')

  const visible = getVisibleRecords()
  const selectedUrls = s.selectedUrls
  const hasSelection = selectedUrls.size > 0

  const recs =
    scope === 'selected' && hasSelection
      ? (() => {
          const known = new Set(s.records.map((r) => r.url))
          return [...selectedUrls]
            .filter((u) => known.has(u))
            .map((u) => s.records.find((r) => r.url === u))
        })()
      : visible

  const onExport = () => {
    if (!recs.length) {
      setStatus('没有可导出的记录', 'err')
      return
    }
    const { content, mime, ext } = exportBookmarks(recs, format)
    downloadFile(`bookmarks.${ext}`, content, mime)
    setStatus(`已导出 ${recs.length} 条为 ${format.toUpperCase()}`)
  }

  const onImport = async (e) => {
    const file = e.target.files[0]
    e.target.value = ''
    if (!file) return
    const text = await file.text()
    const items = parseImportFile(file.name, text)
    if (!items.length) {
      setStatus('文件中未找到 http 开头的 URL', 'err')
      return
    }
    const existing = new Set(s.records.map((r) => r.url))
    const fresh = items.filter((i) => !existing.has(i.url))
    const structured = /\.(html?|csv)$/i.test(file.name)
    setStatus(`导入链接中…（${items.length} 条，不联网）`, 'busy')
    try {
      const data = await apiPost('/api/records/quick', { items })
      if (!data.ok) {
        setStatus('导入失败：' + data.error, 'err')
        return
      }
    } catch (err) {
      setStatus('导入失败：' + err.message, 'err')
      return
    }
    await loadRecords()
    const parts = [`已入库 ${data.inserted} 条`]
    if (data.skipped) parts.push(`跳过 ${data.skipped} 条（已存在）`)
    if (structured) parts.push('标题/标签已从文件读取')
    enqueueBackfill(fresh.map((i) => i.url), parts.join('，'))
  }

  return (
    <div
      className="modal-mask"
      onClick={(e) => {
        if (e.target === e.currentTarget) closeModal()
      }}
    >
      <div className="modal io-modal">
        <div className="modal-title">导入 / 导出</div>

        <div className="io-block">
          <div className="io-block-head">
            <span>导出</span>
            <span className="io-count">{recs.length} 条</span>
          </div>
          <div className="io-export-row">
            <div className="io-seg" role="radiogroup" aria-label="导出范围">
              <button
                type="button"
                className={'io-seg-btn' + (scope === 'visible' ? ' on' : '')}
                onClick={() => setScope('visible')}
              >
                当前可见
              </button>
              <button
                type="button"
                className={'io-seg-btn' + (scope === 'selected' ? ' on' : '')}
                disabled={!hasSelection}
                title={hasSelection ? '' : '先勾选书签再选此项'}
                onClick={() => setScope('selected')}
              >
                已选中{hasSelection ? `（${selectedUrls.size}）` : ''}
              </button>
            </div>
            <select
              className="input io-format"
              value={format}
              onChange={(e) => setFormat(e.target.value)}
              aria-label="导出格式"
            >
              <option value="html">HTML · 浏览器书签</option>
              <option value="csv">CSV · 结构化</option>
              <option value="txt">TXT · 纯链接</option>
            </select>
            <button className="btn-primary" disabled={!recs.length} onClick={onExport}>
              导出
            </button>
          </div>
        </div>

        <div className="io-block">
          <div className="io-block-head">
            <span>导入</span>
          </div>
          <div className="io-import-row">
            <span className="io-hint">支持 HTML / CSV / TXT，不联网，直接写入已有元数据</span>
            <input ref={fileRef} type="file" accept=".txt,.html,.htm,.csv" hidden onChange={onImport} />
            <button className="btn-primary" onClick={() => fileRef.current?.click()}>
              选择文件并导入
            </button>
          </div>
        </div>

        <div className="modal-actions">
          <button className="btn-ghost" onClick={closeModal}>
            关闭
          </button>
        </div>
      </div>
    </div>
  )
}
