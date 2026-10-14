import { useEffect } from 'react'
import { useStore, MIN_GROUP, rootDomain, hostOf, getVisibleRecords, setStatus } from './store.js'
import Card from './Card.jsx'

export default function Board() {
  const s = useStore()
  const visible = getVisibleRecords()

  /* Hooks 必须无条件调用：状态栏文案 + effect 放到「提前返回」之前。
     否则空看板（visible 为空）时少跑一个 useEffect，等真实数据回来 hook 数量
     变化，会触发 React「Rendered more hooks than during the previous render」
     → 抛错卸载整棵树 → 白屏。 */
  const statusParts = [`共 ${s.records.length} 条`]
  if (s.selectedDomain) statusParts.push(s.selectedDomain === '__other__' ? '其他' : s.selectedDomain)
  if (s.selectedTag && s.selectedTag !== '__untagged__') statusParts.push(s.selectedTag)
  if (s.selectedTag === '__untagged__') statusParts.push('未标签')
  statusParts.push(`${visible.length} 条显示`)
  const statusText = statusParts.join('，')
  useEffect(() => {
    if (statusText !== s.status.text) setStatus(statusText)
  })

  if (!visible.length) {
    return <div className="board"><div className="empty-hint">还没有书签，粘贴一个 URL 开始吧</div></div>
  }

  // 按域名分组
  const domainMap = new Map()
  for (const r of visible) {
    const d = rootDomain(hostOf(r.url)) || 'unknown'
    if (!domainMap.has(d)) domainMap.set(d, [])
    domainMap.get(d).push(r)
  }
  if (!s.selectedDomain || s.selectedDomain === '__other__') {
    const major = new Map()
    const minor = []
    for (const [d, recs] of domainMap) {
      if (recs.length < MIN_GROUP) minor.push(...recs)
      else major.set(d, recs)
    }
    if (minor.length) major.set('其他', minor)
    domainMap.clear()
    for (const [d, recs] of major) domainMap.set(d, recs)
  }
  const sorted = [...domainMap.entries()].sort((a, b) => b[1].length - a[1].length)

  if (sorted.length <= 1 && visible.length <= 20) {
    return (
      <div className="board">
        <div className="board-grid">
          {visible.map((r) => (<Card key={r.url} record={r} />))}
        </div>
      </div>
    )
  }

  return (
    <div className="board">
      {sorted.map(([domain, recs]) => (
        <div className="domain-group" key={domain}>
          <div className="domain-header">
            <span className="domain-toggle">▸</span>
            <span className="domain-name">{domain}</span>
            <span className="domain-count">{recs.length}</span>
          </div>
          <div className="domain-grid">
            {recs.map((r) => (<Card key={r.url} record={r} />))}
          </div>
        </div>
      ))}
    </div>
  )
}
