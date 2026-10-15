import { useStore, hostOf, toggleSelect, fetchRecord, removeRecord } from './store.js'
import { openModal } from './ui.js'

function imgSrc(u) {
  const proxy = useStore().globalProxy
  return '/api/img?src=' + encodeURIComponent(u) + (proxy ? '&proxy=' + encodeURIComponent(proxy) : '')
}

// created_at 形如 'YYYY-MM-DD HH:MM:SS'（本地时间）。NULL/未知 → '—'。
function formatTime(ts) {
  if (!ts) return '—'
  const parts = ts.split(' ')
  const date = parts[0] || ''
  const time = (parts[1] || '').slice(0, 5) // HH:MM
  const seg = date.split('-').map(Number)
  if (seg.length !== 3) return ts
  const now = new Date()
  const sameDay = now.getFullYear() === seg[0] && now.getMonth() + 1 === seg[1] && now.getDate() === seg[2]
  if (sameDay) return time
  if (now.getFullYear() === seg[0]) {
    return `${String(seg[1]).padStart(2, '0')}-${String(seg[2]).padStart(2, '0')} ${time}`
  }
  return date
}

export default function Card({ record }) {
  const s = useStore()
  const host = hostOf(record.url)
  const selected = s.selectedUrls.has(record.url)

  const statusClass = !record.fetched ? 'pending' : record.success ? 'ok' : 'fail'
  const statusTitle = !record.fetched ? '还没抓取' : record.success ? '抓取成功' : '抓取失败'

  const cover = record.thumbnail ? imgSrc(record.thumbnail) : null
  const favicon = record.favicon && record.favicon.startsWith('http') ? imgSrc(record.favicon) : null

  return (
    <div className={'card' + (selected ? ' selected' : '')} data-url={record.url}>
      <div className="thumb">
        <input
          className="card-check"
          type="checkbox"
          checked={selected}
          title={record.url}
          onChange={(e) => toggleSelect(record.url, e.target.checked)}
        />
        <a className="open" href={record.url} target="_blank" rel="noopener" title={record.url}>
          {cover ? (
            <img className="cover" src={cover} loading="lazy" alt="" onError={(e) => { e.currentTarget.remove() }} />
          ) : (
            <span className="letter">{host.slice(0, 1).toUpperCase()}</span>
          )}
          {favicon && <img className="favicon" src={favicon} loading="lazy" alt="" onError={(e) => { e.currentTarget.remove() }} />}
        </a>
        <div className="thumb-actions">
          <button title="查看详情" onClick={() => openModal('detail', record)}>详情</button>
          <button title="重新抓取" onClick={() => fetchRecord(record.url)}>重新抓取</button>
        </div>
        <div className="actions">
          <button onClick={() => openModal('rename', record)}>重命名</button>
          <button onClick={() => openModal('tag', record)}>标签</button>
          <button onClick={() => removeRecord(record)}>删除</button>
        </div>
      </div>
      <div className="body">
        <a className="title" href={record.url} target="_blank" rel="noopener" title={record.url}>
          {record.title || '（标题待补充）'}
        </a>
        <div className="domain">
          <span className={'dot ' + statusClass} title={statusTitle} />
          {(record.tags || []).map((t) => (
            <span className="tag-badge" key={t.id || t.name}>{t.name}</span>
          ))}
        </div>
      </div>
      <div className="card-footer">
        <a className="footer-domain" href={record.url} target="_blank" rel="noopener" title={record.url}>
          {host}
        </a>
        <span className="footer-time" title={record.created_at || '导入时间未知'}>
          {formatTime(record.created_at)}
        </span>
      </div>
    </div>
  )
}
