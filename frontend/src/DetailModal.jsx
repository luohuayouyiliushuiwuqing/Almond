import { useEffect, useState } from 'react'
import { useStore, fetchRecord } from './store.js'
import { closeModal } from './ui.js'

export default function DetailModal({ record }) {
  const s = useStore()
  const [loading, setLoading] = useState(false)
  // 跟随详情目标的最新引用
  const cur = s.detailTarget && s.detailTarget.url === record.url ? s.detailTarget : record
  const r = cur

  const refresh = async () => {
    setLoading(true)
    try {
      await fetchRecord(r.url)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    if (!r.fetched) refresh()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  return (
    <div className="modal-mask" onClick={(e) => { if (e.target === e.currentTarget) closeModal() }}>
      <div className="modal detail-modal">
        <div className="modal-title">详情</div>
        <div className="detail-domain">{r.url}</div>
        <div className="detail-fields">
          {loading && <div className="detail-empty">正在抓取详情…（首次需要数秒）</div>}
          {!loading && !r.fetched && <div className="detail-empty">详情尚未抓取，点「重新抓取」或再次打开本弹层</div>}
          {!loading && r.fetched && !r.success && <div className="detail-empty">该记录抓取失败，点「重新抓取」再试</div>}
          {!loading && r.fetched && r.success && (!r.details || !r.details.length) && (
            <div className="detail-empty">未提取到详情字段——该域名可在域名设置里配置详情选择器，或点「重新抓取」更新</div>
          )}
          {!loading && r.fetched && r.success && r.details && r.details.length > 0 && renderDetails(r.details)}
        </div>
        <div className="modal-actions">
          <button className="btn-ghost" disabled={loading} onClick={refresh}>重新抓取</button>
          <button className="btn-primary" onClick={closeModal}>关闭</button>
        </div>
      </div>
    </div>
  )
}

function renderDetails(details) {
  const rows = []
  let i = 0
  while (i < details.length) {
    const f = details[i]
    if (!f.label) {
      const chips = []
      while (i < details.length && !details[i].label) {
        const g = details[i++]
        const first = g.links && g.links[0]
        if (first) {
          chips.push(
            <a className="detail-chip" key={i} href={first.href} target="_blank" rel="noopener" title={first.href}>
              {g.value || first.text || first.href}
            </a>,
          )
        } else {
          chips.push(<span className="detail-chip" key={i}>{g.value}</span>)
        }
      }
      rows.push(
        <div className="detail-row" key={'row' + i}>
          <span className="detail-label">标签</span>
          <span className="detail-chips">{chips}</span>
        </div>,
      )
      continue
    }
    const dateText = f.datetime ? f.datetime.slice(0, 10) : ''
    rows.push(
      <div className="detail-row" key={i}>
        <span className="detail-label">{f.label}</span>
        <span className="detail-value">
          {f.links && f.links.length
            ? f.links.map((l, k) => (<a key={k} href={l.href} target="_blank" rel="noopener">{l.text || l.href}</a>))
            : (f.value || dateText)}
        </span>
      </div>,
    )
    i++
  }
  return rows
}
