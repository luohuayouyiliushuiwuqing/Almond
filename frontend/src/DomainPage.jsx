import { useState } from 'react'
import {
  useStore, rootDomain, hostOf, getVisibleRecords, readPrefs, savePrefs,
  leaveDomainPage, loadDomainConfig, applyPrefsTag, toggleSelect,
} from './store.js'
import { openModal } from './ui.js'
import { fetchRecord, removeRecord } from './store.js'
import Card from './Card.jsx'
import DomainSettings from './DomainSettings.jsx'

export default function DomainPage() {
  const s = useStore()
  const domain = s.domainPage
  const [settingsOpen, setSettingsOpen] = useState(false)
  const cfg = s.domainConfig.get(domain) || {}
  const prefs = readPrefs(cfg)

  const visible = getVisibleRecords()
  const cmp = { default: null, title: (a, b) => (a.title || '').localeCompare(b.title || '', 'zh'), url: (a, b) => a.url.localeCompare(b.url), status: (a, b) => statusRank(a) - statusRank(b) }[prefs.sort]
  const recs = cmp ? visible.slice().sort(cmp) : visible

  const all = s.records.filter((r) => r.domain === domain)
  const pending = all.filter((r) => !r.fetched).length
  const failed = all.filter((r) => r.fetched && !r.success).length
  const tagNames = new Set()
  for (const r of all) for (const t of r.tags || []) tagNames.add(t.name)

  const patchPref = (patch) => {
    const next = { ...prefs, ...patch }
    savePrefs(domain, next)
    s.selectedTag = next.tag || null
    applyPrefsTag()
  }

  return (
    <div className="board domain-page">
      <section className="domain-header-bar" hidden={false}>
        <div className="dp-head">
          <button className="btn-ghost dp-back" onClick={() => { setSettingsOpen(false); leaveDomainPage() }}>← 返回看板</button>
          <div className="dp-head-mid">
            <h2 className="dp-title">{domain}</h2>
            {cfg.display_name && <span className="dp-alias">别名：{cfg.display_name}</span>}
            <div className="dp-stats">共 {all.length} 条 · 显示 {visible.length} 条 · 待抓 {pending} · 失败 {failed} · 标签 {tagNames.size}</div>
          </div>
          <button className="btn-ghost dp-set-toggle" onClick={() => setSettingsOpen((v) => !v)}>
            {settingsOpen ? '收起设置' : '域名设置'}
          </button>
        </div>
        {settingsOpen && (
          <div className="dp-settings">
            <DomainSettings
              key={domain}
              domain={domain}
              cfg={cfg}
              onSaved={async () => { await loadDomainConfig() }}
            />
          </div>
        )}
      </section>

      <div className="dp-toolbar">
        <select className="input dp-sort" value={prefs.view} onChange={(e) => patchPref({ view: e.target.value })}>
          <option value="card">视图：卡片</option>
          <option value="list">视图：列表</option>
        </select>
        <select className="input dp-sort" value={prefs.sort} onChange={(e) => patchPref({ sort: e.target.value })}>
          <option value="default">排序：默认（入库顺序）</option>
          <option value="title">排序：标题</option>
          <option value="url">排序：链接</option>
          <option value="status">排序：状态（未抓→失败→成功）</option>
        </select>
        {prefs.view === 'list' && (
          <>
            {[['tags', '标签'], ['details', '详情摘要']].map(([key, labelText]) => (
              <label className="dp-field-toggle" key={key}>
                <input
                  type="checkbox"
                  checked={prefs.fields.includes(key)}
                  onChange={(e) => patchPref({ fields: e.target.checked ? [...new Set([...prefs.fields, key])] : prefs.fields.filter((f) => f !== key) })}
                />
                {labelText}
              </label>
            ))}
          </>
        )}
        <select className="input dp-tag" value={s.selectedTag || ''} onChange={(e) => patchPref({ tag: e.target.value || '' })}>
          <option value="">标签：全部</option>
          <option value="__untagged__">标签：未标签</option>
          {[...tagNames].sort().map((n) => (<option key={n} value={n}>{n}</option>))}
        </select>
      </div>

      {!recs.length ? (
        <div className="dp-empty">该域名下没有符合当前条件的记录</div>
      ) : prefs.view === 'card' ? (
        <div className="domain-grid">
          {recs.map((r) => (<Card key={r.url} record={r} />))}
        </div>
      ) : (
        <div className="dp-list">
          {recs.map((r) => (<Row key={r.url} record={r} prefs={prefs} />))}
        </div>
      )}
    </div>
  )
}

function statusRank(r) {
  if (!r.fetched) return 0
  if (!r.success) return 1
  return 2
}

function Row({ record, prefs }) {
  const s = useStore()
  const selected = s.selectedUrls.has(record.url)
  const statusClass = !record.fetched ? 'pending' : record.success ? 'ok' : 'fail'
  return (
    <div className={'dp-row' + (selected ? ' selected' : '')} data-url={record.url}>
      <input
        className="card-check"
        type="checkbox"
        checked={selected}
        onChange={(e) => toggleSelect(record.url, e.target.checked)}
      />
      <span className={'dot ' + statusClass} title={!record.fetched ? '还没抓取' : record.success ? '抓取成功' : '抓取失败'} />
      <div className="dp-main">
        <a className="dp-link" href={record.url} target="_blank" rel="noopener" title={record.url}>
          {record.title || record.url}
        </a>
        <div className="dp-meta">
          {prefs.fields.includes('tags') && record.tags && record.tags.length > 0 && (
            <span className="dp-tags">
              {record.tags.map((t) => (<span className="tag-badge" key={t.id || t.name}>{t.name}</span>))}
            </span>
          )}
          {prefs.fields.includes('details') && record.details && record.details.length > 0 && (
            <span className="dp-brief">{briefText(record)}</span>
          )}
        </div>
      </div>
      <div className="dp-acts">
        <button className="dp-act" onClick={() => openModal('detail', record)}>详情</button>
        <button className="dp-act" onClick={() => fetchRecord(record.url)}>重抓</button>
        <button className="dp-act" onClick={() => openModal('rename', record)}>改名</button>
        <button className="dp-act" onClick={() => openModal('tag', record)}>标签</button>
        <button className="dp-act" onClick={() => removeRecord(record)}>删</button>
      </div>
    </div>
  )
}

function briefText(record) {
  return (record.details || []).slice(0, 2)
    .map((d) => {
      const v = d.value || (d.links && d.links[0] && d.links[0].text) || ''
      return d.label ? `${d.label}：${v}` : v
    })
    .join('　·　')
}
