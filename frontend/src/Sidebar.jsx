import { useStore, MIN_GROUP, rootDomain, hostOf, leaveDomainPage, goToDomain } from './store.js'

function filterTo(apply) {
  leaveDomainPage()
  apply()
}

export default function Sidebar() {
  const s = useStore()
  const domainCounts = new Map()
  for (const r of s.records) {
    const d = rootDomain(hostOf(r.url)) || 'unknown'
    domainCounts.set(d, (domainCounts.get(d) || 0) + 1)
  }
  const sortedDomains = [...domainCounts.entries()].sort((a, b) => b[1] - a[1])

  const minorNames = []
  let minorTotal = 0
  let minorProxied = 0
  const major = []
  for (const [domain, count] of sortedDomains) {
    if (count < MIN_GROUP) {
      minorNames.push(domain)
      minorTotal += count
      if (s.proxyDomains.has(domain)) minorProxied++
    } else major.push([domain, count])
  }

  const tagCounts = new Map()
  let untagged = 0
  for (const r of s.records) {
    if (!r.tags || !r.tags.length) { untagged++; continue }
    for (const t of r.tags) tagCounts.set(t.name, (tagCounts.get(t.name) || 0) + 1)
  }
  const sortedTags = [...tagCounts.entries()].sort((a, b) => b[1] - a[1])

  const allActive = !s.domainPage && s.selectedDomain === null && s.selectedTag === null

  return (
    <aside className={'sidebar' + (s.sidebarCollapsed ? ' collapsed' : '')}>
      <div
        className={'sidebar-item' + (allActive ? ' active' : '')}
        onClick={() => filterTo(() => { s.selectedDomain = null; s.selectedTag = null })}
      >
        <span className="sidebar-name">全部</span>
        <span className="sidebar-count">{s.records.length}</span>
      </div>

      <div className="sidebar-label">域名</div>
      {major.map(([domain, count]) => {
        const active = s.domainPage === domain
        const proxied = s.proxyDomains.has(domain)
        return (
          <div
            key={domain}
            className={'sidebar-item' + (active ? ' active' : '') + (proxied ? ' proxy' : '')}
            title={active ? '再点一次返回看板' : `进入 ${domain} 的子页面`}
            onClick={() => {
              if (s.domainPage === domain) leaveDomainPage()
              else goToDomain(domain)
            }}
          >
            <span className="sidebar-name">{domain}</span>
            {proxied && <span className="proxy-badge" title="匹配代理规则：走代理">代理</span>}
            <span className="sidebar-count">{count}</span>
          </div>
        )
      })}
      {minorNames.length > 0 && (
        <div
          className={'sidebar-item' + (s.selectedDomain === '__other__' ? ' active' : '') + (minorProxied > 0 ? ' proxy' : '')}
          onClick={() => filterTo(() => { s.selectedDomain = s.selectedDomain === '__other__' ? null : '__other__' })}
        >
          <span className="sidebar-name">其他</span>
          {minorProxied > 0 && (
            <span className="proxy-badge" title={`包含 ${minorProxied} 个走代理的域名`}>代理</span>
          )}
          <span className="sidebar-count">{minorTotal}</span>
        </div>
      )}

      <div className="sidebar-label">标签</div>
      {sortedTags.map(([name, count]) => {
        const active = s.selectedTag === name
        return (
          <div
            key={name}
            className={'sidebar-item' + (active ? ' active' : '')}
            onClick={() => filterTo(() => { s.selectedTag = active ? null : name })}
          >
            <span className="sidebar-name">{name}</span>
            <span className="sidebar-count">{count}</span>
          </div>
        )
      })}
      {untagged > 0 && (
        <div
          className={'sidebar-item' + (s.selectedTag === '__untagged__' ? ' active' : '')}
          onClick={() => filterTo(() => { s.selectedTag = s.selectedTag === '__untagged__' ? null : '__untagged__' })}
        >
          <span className="sidebar-name">未标签</span>
          <span className="sidebar-count">{untagged}</span>
        </div>
      )}
    </aside>
  )
}
