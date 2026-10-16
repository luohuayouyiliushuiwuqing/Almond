import { useEffect, useState } from 'react'
import {
  useStore, detectProxy, saveGlobalProxy, addRule, removeRule,
  patchNeedProxy, refreshTests, retryDomain, goToDomain,
} from './store.js'
import { apiGet } from './api.js'
import { closeModal } from './ui.js'

/* 系统实测结论 → 徽标文案 + 配色 + 悬浮说明（与后端 fetch_hints 字段对应） */
function verdict(t) {
  if (!t) return { text: '未测试', cls: 'none', title: '系统还没抓取过这个域名；自动抓取前会先自动确认方案' }
  if (t.confirm && t.confirm.ok === false) {
    return { text: '无方案', cls: 'bad', title: `系统实测：${t.confirm.reason || '直连/代理/JS 多路尝试均未取得数据'}（${t.confirm.at || '时间未知'}）——自动抓取整组跳过` }
  }
  // 域名定义兜底方案（实测不通，但按 need_proxy 规则 / 国内外能定出路由）：放行整组、标注推断
  if (t.scheme === 'direct' && !t.strategy) {
    return { text: '直连·推断', cls: 'ok', title: '域名定义方案：按直连抓取（国内 / 强制直连，未经实测）——自动抓取放行' }
  }
  if (t.scheme === 'proxy' && !t.strategy) {
    return { text: '代理·推断', cls: 'proxy', title: '域名定义方案：按代理抓取（国外 / 强制代理，未经实测）——自动抓取放行' }
  }
  if (t.confirm && t.plan !== true) {
    return { text: '方案失效', cls: 'bad', title: `系统实测：确认过（${t.confirm.at || '时间未知'}）但当前无可用方案——自动抓取跳过，人工强制补抓仍可试` }
  }
  if (t.direct === true) return { text: '直连', cls: 'ok', title: `系统实测：直连可达（${t.updated || '时间未知'}）` }
  if (t.direct === false && t.proxy === true) return { text: '代理', cls: 'proxy', title: `系统实测：直连不通、经代理可达（${t.updated || '时间未知'}）` }
  if (t.direct === false) return { text: '须代理', cls: 'warn', title: `系统实测：直连不通，代理尚未验证可用（${t.updated || '时间未知'}）` }
  if (t.proxy === true) return { text: '代理', cls: 'proxy', title: `系统实测：经代理可达（${t.updated || '时间未知'}）` }
  return { text: '未测试', cls: 'none', title: '没有直连实测结论' }
}

export default function ProxyPanel() {
  const s = useStore()
  const [rules, setRules] = useState([])
  const [domains, setDomains] = useState([])
  const [globalProxy, setGlobalProxy] = useState(s.globalProxy || '')
  const [pattern, setPattern] = useState('')
  const [needProxy, setNeedProxy] = useState('1')
  const [selDomain, setSelDomain] = useState('')
  const [selNeed, setSelNeed] = useState('1')
  const [refreshing, setRefreshing] = useState(false)
  const [retrying, setRetrying] = useState({})

  const load = async () => {
    try {
      const rd = await apiGet('/api/proxy-rules')
      setRules(rd.rules || [])
    } catch (e) { setRules([]) }
    try {
      const dd = await apiGet('/api/domains')
      setDomains((dd.domains || []).slice().sort((a, b) => a.name.localeCompare(b.name)))
    } catch (e) { setDomains([]) }
  }
  useEffect(() => { load() }, [])

  const isSpecified = (d) => d.need_proxy !== null && d.need_proxy !== undefined
  const specified = domains.filter(isSpecified)
  const unspec = domains.filter((d) => !isSpecified(d))
  const testedCount = domains.filter((d) => d.tested).length
  const rightOrder = domains.slice().sort(
    (a, b) => (b.tested ? 1 : 0) - (a.tested ? 1 : 0) || a.name.localeCompare(b.name),
  )

  const onSaveGlobal = () => saveGlobalProxy(globalProxy.trim())
  const onDetect = () => detectProxy({ fill: true })
  const onAddRule = () => {
    const p = pattern.trim()
    if (!p) return
    addRule(p, needProxy === '1')
    setPattern('')
  }
  const onRefresh = async () => {
    setRefreshing(true)
    try { await refreshTests(); await load() } finally { setRefreshing(false) }
  }
  const onRetry = async (name) => {
    setRetrying((r) => ({ ...r, [name]: true }))
    try { await retryDomain(name); await load() }
    finally { setRetrying((r) => { const n = { ...r }; delete n[name]; return n }) }
  }
  const onSpecify = () => {
    if (!selDomain) return
    patchNeedProxy(selDomain, selNeed === '1')
    setSelDomain('')
  }

  return (
    <div className="modal-mask" onClick={(e) => { if (e.target === e.currentTarget) closeModal() }}>
      <div className="modal proxy-panel">
        <div className="modal-title">代理映射</div>

        <div className="proxy-section">
          <label>全局代理</label>
          <div className="proxy-row">
            <input
              className="input grow"
              style={{ flex: 1 }}
              placeholder="http://127.0.0.1:7890 （留空 = 直连）"
              value={globalProxy}
              onChange={(e) => setGlobalProxy(e.target.value)}
            />
            <button className="btn-ghost" onClick={onDetect}>探测</button>
            <button className="btn-primary" onClick={onSaveGlobal}>保存</button>
          </div>
          <div className="proxy-hint">
            服务端每 30s 自动重探本机代理端口（7889-7899）并刷新；点「保存」即视为手动指定，之后不再被自动探测覆盖。
          </div>
        </div>

        <div className="proxy-body">
          {/* 左：系统实测（只读徽标，整行点击进子页） */}
          <div className="proxy-side">
            <div className="proxy-col-title">
              <span>系统实测</span>
              <span className="proxy-col-count">{testedCount}/{domains.length}</span>
              <button className="test-refresh-btn" disabled={refreshing} onClick={onRefresh}>
                {refreshing ? '确认中…' : '刷新'}
              </button>
            </div>
            <div className="test-list">
              {rightOrder.map((d) => {
                const v = verdict(d.tested)
                return (
                  <div className="rule-row test-row" key={d.name} onClick={() => { closeModal(); goToDomain(d.name) }}>
                    <span className="rule-pattern link" title={`${v.title}；点击进入 ${d.name} 子页`}>{d.name}</span>
                    <span className={`test-badge ${v.cls}`} title={v.title}>{v.text}</span>
                    <button
                      className={'test-retry-btn' + (retrying[d.name] ? ' busy' : '')}
                      disabled={!!retrying[d.name]}
                      title={`重测 ${d.name} 的抓取方案（直连/代理）`}
                      onClick={(ev) => { ev.stopPropagation(); onRetry(d.name) }}
                    >
                      <span className="retry-glyph">↻</span>
                    </button>
                  </div>
                )
              })}
              {!domains.length && <div className="rule-empty">还没有任何域名</div>}
            </div>
            <div className="proxy-hint">整行点击进入该域名子页；右侧「指定」可改用户路由。</div>
          </div>

          {/* 右：用户指定的全部关系 */}
          <div className="proxy-main">
            <div className="proxy-section">
              <label>URL 模式规则</label>
              <div className="rule-list">
                {rules.map((rule) => (
                  <div className="rule-row" key={rule.id}>
                    <span className="rule-pattern">{rule.pattern}</span>
                    <span className={'rule-proxy' + (rule.need_proxy ? '' : ' direct')}>
                      {rule.need_proxy ? '用代理' : '直连'}
                    </span>
                    <button className="rule-del" onClick={() => removeRule(rule.id)}>删除</button>
                  </div>
                ))}
                {!rules.length && <div className="rule-empty">暂无规则</div>}
              </div>
              <div className="proxy-row proxy-add">
                <input
                  className="input"
                  placeholder="匹配模式，如 *.youtube.com"
                  value={pattern}
                  onChange={(e) => setPattern(e.target.value)}
                  onKeyDown={(e) => { if (e.key === 'Enter') onAddRule() }}
                />
                <select className="input rule-select" value={needProxy} onChange={(e) => setNeedProxy(e.target.value)}>
                  <option value="1">用代理</option>
                  <option value="0">直连</option>
                </select>
                <button className="btn-ghost" onClick={onAddRule}>添加规则</button>
              </div>
            </div>

            <div className="proxy-section">
              <label>域名路由（用户指定，覆盖全局）</label>
              <div className="rule-list">
                {specified.map((d) => (
                  <div className="rule-row route-row" key={d.name}>
                    <span
                      className="rule-pattern link"
                      title="点域名名：进入该域名子页"
                      onClick={() => { closeModal(); goToDomain(d.name) }}
                    >{d.name}</span>
                    <select
                      className="input rule-need"
                      value={d.need_proxy ? '1' : '0'}
                      onChange={(e) => patchNeedProxy(d.name, e.target.value === '1')}
                    >
                      <option value="1">用代理</option>
                      <option value="0">直连</option>
                    </select>
                    <button className="rule-del" onClick={() => patchNeedProxy(d.name, null)}>取消</button>
                  </div>
                ))}
                {!specified.length && <div className="rule-empty">还没有指定任何域名——从下方选择添加</div>}
              </div>
              <div className="proxy-row proxy-add">
                <select className="input rule-select rule-domain" value={selDomain} onChange={(e) => setSelDomain(e.target.value)}>
                  <option value="">选择域名…</option>
                  {unspec.map((d) => (<option key={d.name} value={d.name}>{d.name}</option>))}
                </select>
                <select className="input rule-need" value={selNeed} onChange={(e) => setSelNeed(e.target.value)}>
                  <option value="1">用代理</option>
                  <option value="0">直连</option>
                </select>
                <button className="btn-ghost" disabled={!selDomain} onClick={onSpecify}>指定</button>
              </div>
            </div>
          </div>
        </div>

        <div className="modal-actions">
          <button className="btn-ghost" onClick={closeModal}>关闭</button>
        </div>
      </div>
    </div>
  )
}
