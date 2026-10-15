import { useRef } from 'react'
import {
  useStore, addUrl, setStatus, handleBackfillClick, enqueueBackfill,
  loadRecords, setFilter, getVisibleRecords, toggleSidebar,
} from './store.js'
import { openModal } from './ui.js'
import { apiPost } from './api.js'

/* 工具栏：URL 添加 / 导入 / 导出 / 代理 / 存储 / 补抓 / 搜索 */
export default function Toolbar() {
  const s = useStore()
  const urlRef = useRef(null)
  const fileRef = useRef(null)
  const b = s.backfill

  const onAdd = () => {
    const v = urlRef.current?.value || ''
    if (!v.trim()) return
    urlRef.current.value = ''
    addUrl(v)
  }
  const onImport = async (e) => {
    const file = e.target.files[0]
    e.target.value = ''
    if (!file) return
    const text = await file.text()
    const isHtml = /\.html?$/i.test(file.name)
    const seen = new Set()
    const items = []
    const push = (it) => {
      if (!/^https?:\/\//i.test(it.url) || seen.has(it.url)) return
      seen.add(it.url)
      items.push(it)
    }
    if (isHtml) {
      const doc = new DOMParser().parseFromString(text, 'text/html')
      for (const a of doc.querySelectorAll('a[href]')) {
        push({
          url: a.href,
          title: (a.textContent || '').replace(/\s+/g, ' ').trim(),
          thumbnail: a.getAttribute('data-cover') || '',
          favicon: '',
          tags: (a.getAttribute('tags') || '').split(',').map((x) => x.trim()).filter(Boolean),
        })
      }
    } else {
      for (const line of text.split(/\r?\n/)) push({ url: line.trim() })
    }
    if (!items.length) {
      setStatus('文件中未找到 http 开头的 URL', 'err')
      return
    }
    const existing = new Set(s.records.map((r) => r.url))
    const fresh = items.filter((i) => !existing.has(i.url))
    setStatus(`导入链接中…（${items.length} 条，不联网）`, 'busy')
    try {
      const data = await apiPost('/api/records/quick', { items })
      if (!data.ok) {
        setStatus('导入失败：' + data.error, 'err')
        return
      }
    } catch (e) {
      setStatus('导入失败：' + e.message, 'err')
      return
    }
    await loadRecords()
    const parts = [`已入库 ${data.inserted} 条`]
    if (data.skipped) parts.push(`跳过 ${data.skipped} 条（已存在）`)
    if (isHtml) parts.push('标题/封面已从文件读取')
    enqueueBackfill(fresh.map((i) => i.url), parts.join('，'))
  }

  const doExport = (format) => {
    const recs = getVisibleRecords()
    if (!recs.length) {
      setStatus('没有可导出的记录', 'err')
      return
    }
    if (format === 'txt') {
      downloadFile('bookmarks.txt', recs.map((r) => r.url).join('\n') + '\n', 'text/plain;charset=utf-8')
    } else {
      const now = Math.floor(Date.now() / 1000)
      const esc = (x) => String(x || '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;')
      const lines = recs.map((r) => {
        const tags = (r.tags || []).map((t) => t.name).join(',')
        const tagAttr = tags ? ` TAGS="${esc(tags)}"` : ''
        return `    <DT><A HREF="${esc(r.url)}" ADD_DATE="${now}"${tagAttr}>${esc(r.title || r.url)}</A>`
      })
      const html = [
        '<!DOCTYPE NETSCAPE-Bookmark-file-1>',
        '<META HTTP-EQUIV="Content-Type" CONTENT="text/html; charset=UTF-8">',
        '<TITLE>Bookmarks</TITLE><H1>Bookmarks</H1><DL><p>',
        ...lines,
        '</DL><p>',
        '',
      ].join('\n')
      downloadFile('bookmarks.html', html, 'text/html;charset=utf-8')
    }
    setStatus(`已导出 ${recs.length} 条为 ${format.toUpperCase()}`)
  }

  const [bfLabel, bfDisabled, bfTitle] = backfillButtonLabel(s)

  return (
    <header className="toolbar">
      <button className="btn-icon" title="收起/展开侧边栏" onClick={toggleSidebar}>
        ☰
      </button>
      <div className="brand">
        <span className="brand-dot" />
        Almond
      </div>
      <input ref={urlRef} className="input grow" placeholder="粘贴 URL 后回车添加…" spellCheck={false}
        onKeyDown={(e) => { if (e.key === 'Enter') onAdd() }} />
      <button className="btn-primary" onClick={onAdd}>添加</button>
      <button className="btn-ghost" onClick={() => fileRef.current?.click()}>导入</button>
      <input ref={fileRef} type="file" accept=".txt,.html,.htm" hidden onChange={onImport} />
      <select className="input export-select" title="导出当前可见记录" defaultValue=""
        onChange={(e) => { const f = e.target.value; e.target.value = ''; if (f) doExport(f) }}>
        <option value="">导出</option>
        <option value="txt">TXT</option>
        <option value="html">HTML</option>
      </select>
      <button className="btn-ghost" onClick={() => openModal('proxy')}>代理</button>
      <button className="btn-ghost" onClick={() => openModal('storage')}>存储</button>
      <button className="btn-ghost" disabled={bfDisabled} onClick={handleBackfillClick} title={bfTitle}>
        {bfLabel}
      </button>
      <input className="input search" placeholder="搜索标题或链接…"
        onInput={(e) => setFilter(e.target.value.trim().toLowerCase())} />
    </header>
  )
}

function downloadFile(fileName, content, mime) {
  const blob = new Blob([content], { type: mime })
  const a = document.createElement('a')
  a.href = URL.createObjectURL(blob)
  a.download = fileName
  document.body.appendChild(a)
  a.click()
  a.remove()
  URL.revokeObjectURL(a.href)
}

/* 复制 vanilla paintButton 的按钮文案逻辑 */
function backfillButtonLabel(s) {
  const b = s.backfill
  const left = b.urls.length
  const busy = b.running || left > 0
  if (busy) {
    if (b.paused) return [`继续补抓 ${left}`, false, '点击继续补抓']
    return [`${b.forceRun ? '强制' : ''}补抓 ${b.total - left}/${b.total}`, false, '点击暂停补抓']
  }
  let pending = 0
  let failed = 0
  for (const r of s.records) {
    if (!r.fetched) {
      const cfg = s.domainConfig.get(rootDomainSafe(r.url))
      if (cfg && cfg.tested && cfg.tested.plan === true && !(cfg.auto_fetch === false)) pending++
    } else if (!r.success) failed++
  }
  if (!pending && !failed) return ['补抓', true, '已全部抓取完成，暂无需要补抓的记录']
  return [
    pending && failed ? `补抓 ${pending} · 重试 ${failed}` : pending ? `补抓 ${pending}` : `重试 ${failed}`,
    false,
    '点击强制补抓：从未抓过的和抓取失败的一起重跑',
  ]
}
function rootDomainSafe(url) {
  try {
    return new URL(url).host.replace(/:\d+$/, '').toLowerCase()
  } catch (e) {
    return url
  }
}
