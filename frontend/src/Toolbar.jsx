import { useRef } from 'react'
import {
  useStore, addUrl, handleBackfillClick, setFilter, toggleSidebar,
} from './store.js'
import { openModal } from './ui.js'

/* 工具栏：URL 添加 / 代理 / 存储 / 补抓 / 搜索（导入导出收进二级页面「导入导出」） */
export default function Toolbar() {
  const s = useStore()
  const urlRef = useRef(null)
  const b = s.backfill

  const onAdd = () => {
    const v = urlRef.current?.value || ''
    if (!v.trim()) return
    urlRef.current.value = ''
    addUrl(v)
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
      <button className="btn-ghost" onClick={() => openModal('import_export')}>导入导出</button>
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
