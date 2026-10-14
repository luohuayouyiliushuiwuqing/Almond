/* 全局状态 + 全部业务动作（对应原生前端的 state.js / records.js / fetch.js /
   backfill.js / proxy-panel.js / storage.js / modals.js / selectbar.js）。
   用 version 计数触发 React 重渲；组件用 useStore() 读 store 对象。 */

import { useSyncExternalStore } from 'react'
import {
  apiGet, apiPost, apiPatch, apiDel, fetchJob, abortAll, _bindUpdate,
} from './api.js'

/* ---------- 可观察仓库 ---------- */
export const store = {
  records: [],
  filter: '',
  selectedDomain: null,
  selectedTag: null,
  selectedUrls: new Set(),
  domainPage: null,
  domainConfig: new Map(),
  proxyDomains: new Set(),
  storageEpoch: null,
  globalProxy: '',
  proxyManual: false,
  status: { text: '加载中…', kind: '' },
  backfill: {
    urls: [], total: 0, ok: 0, fail: 0, skip: 0, running: false, paused: false,
    inflight: new Set(), gen: 0, forceUrls: new Set(), forceRun: false, note: '',
  },
  // UI 临时态
  detailTarget: null,
  renameTarget: null,
  tagTargets: null,
  sidebarExpanded: false,
}

let version = 0
const listeners = new Set()
export function update(patch) {
  if (patch) Object.assign(store, patch)
  version++
  listeners.forEach((l) => l())
}
export function subscribe(cb) {
  listeners.add(cb)
  return () => listeners.delete(cb)
}
export function useStore() {
  useSyncExternalStore(subscribe, () => version, () => version)
  return store
}

// 把 update 反向注入 api.js（解除循环依赖）
_bindUpdate(update)

/* ---------- 工具 ---------- */
export const MIN_GROUP = 10

export function setStatus(text, kind) {
  update({ status: { text, kind: kind || '' } })
}

export function hostOf(url) {
  try {
    return new URL(url).host || url
  } catch (e) {
    return url
  }
}

const MULTI_TLDS = [
  '.co.uk', '.org.uk', '.ac.uk', '.gov.uk', '.me.uk',
  '.com.cn', '.net.cn', '.org.cn', '.gov.cn', '.edu.cn',
  '.co.jp', '.ne.jp', '.or.jp', '.ac.jp',
  '.com.au', '.net.au', '.org.au',
  '.co.nz', '.com.hk', '.com.tw', '.com.sg',
  '.com.br', '.com.mx', '.co.kr', '.co.in', '.com.ar',
]
export function rootDomain(host) {
  const h = (host || '').replace(/:\d+$/, '').toLowerCase()
  for (const tld of MULTI_TLDS) {
    if (h.endsWith(tld)) {
      const head = h.slice(0, -tld.length)
      return head ? head.split('.').pop() + tld : h
    }
  }
  const parts = h.split('.')
  return parts.length <= 2 ? h : parts.slice(-2).join('.')
}

/* ---------- 数据加载 ---------- */
const PAGE = 500

export function setFilter(v) {
  update({ filter: v })
}

export async function loadRecords() {
  store.records = []
  try {
    let offset = 0
    let first = true
    let total = 0
    while (true) {
      const data = await apiGet(`/api/records?offset=${offset}&limit=${PAGE}`)
      if (data.error) {
        setStatus('加载失败：' + data.error, 'err')
        return
      }
      if (typeof data.epoch === 'number') store.storageEpoch = data.epoch
      const items = data.items ?? data.records ?? []
      store.records.push(...items)
      total = typeof data.total === 'number' ? data.total : store.records.length
      if (first) {
        update({ records: store.records.slice() })
        setStatus(`共 ${total} 条`)
        first = false
      }
      if (!data.has_more) break
      offset += PAGE
    }
    update({ records: store.records.slice() })
    setStatus(`共 ${total} 条`)
  } catch (e) {
    setStatus('加载失败：' + e.message, 'err')
  }
}

export async function loadDomainConfig() {
  try {
    const data = await apiGet('/api/domains')
    store.domainConfig = new Map((data.domains || []).map((d) => [d.name, d]))
    update({ domainConfig: store.domainConfig })
  } catch (e) {
    /* keep */
  }
}

export async function loadProxyDomains() {
  try {
    const data = await apiGet('/api/proxy-domains')
    store.proxyDomains = new Set(data.domains || [])
    update({ proxyDomains: store.proxyDomains })
  } catch (e) {
    store.proxyDomains = new Set()
    update({ proxyDomains: store.proxyDomains })
  }
}

export async function reloadAll() {
  await Promise.all([loadRecords(), loadDomainConfig(), loadProxyDomains()])
}

/* 原地合并（与 vanilla 同义：render 持有引用，换对象会让懒建/闭包拿到旧数据） */
export function upsert(record) {
  const i = store.records.findIndex((r) => r.url === record.url)
  if (i >= 0) Object.assign(store.records[i], record)
  else store.records.unshift(record)
  update({ records: store.records.slice() })
}

export function matchesFilter(record) {
  if (!store.filter) return true
  const hay = `${record.title || ''} ${record.url}`.toLowerCase()
  return hay.includes(store.filter)
}
export function matchesTag(record) {
  if (!store.selectedTag) return true
  if (store.selectedTag === '__untagged__') return !record.tags || !record.tags.length
  return !!record.tags && record.tags.some((t) => t.name === store.selectedTag)
}

export function getVisibleRecords() {
  let visible = store.records.filter(matchesFilter)
  if (store.domainPage) {
    visible = visible.filter((r) => r.domain === store.domainPage)
  } else if (store.selectedDomain === '__other__') {
    const counts = new Map()
    for (const r of store.records) {
      const d = rootDomain(hostOf(r.url)) || 'unknown'
      counts.set(d, (counts.get(d) || 0) + 1)
    }
    const minorSet = new Set(
      [...counts.entries()].filter(([, c]) => c < MIN_GROUP).map(([d]) => d),
    )
    visible = visible.filter((r) => minorSet.has(rootDomain(hostOf(r.url)) || 'unknown'))
  } else if (store.selectedDomain) {
    visible = visible.filter((r) => rootDomain(hostOf(r.url)) === store.selectedDomain)
  }
  visible = visible.filter(matchesTag)
  return visible
}

/* ---------- 抓取 / 添加 ---------- */
export async function fetchRecord(url) {
  const proxy = store.globalProxy
  setStatus(`正在抓取 ${url} …（三级降级，可能需要数秒到一两分钟）`, 'busy')
  try {
    const data = await fetchJob('/api/fetch', { url, proxy })
    if (data.stale) {
      setStatus('存储已切换，本次抓取结果已丢弃')
      return null
    }
    if (data.aborted) {
      setStatus('抓取已中止（存储目录已切换）')
      return null
    }
    if (!data.ok) {
      setStatus('抓取失败：' + data.error, 'err')
      return null
    }
    const existed = store.records.some((r) => r.url === data.record.url)
    upsert(data.record)
    if (store.detailTarget && store.detailTarget.url === data.record.url) {
      update({ detailTarget: data.record })
    }
    const fetched = data.success === undefined ? !!data.record.success : data.success
    setStatus(
      fetched ? `已抓取：${data.record.title || url}` : `三级抓取全部失败，已仅保存 URL：${url}`,
      fetched ? '' : 'err',
    )
    return data.record
  } catch (e) {
    setStatus('请求失败：' + e.message, 'err')
    return null
  }
}

export function addUrl(raw) {
  let url = (raw || '').trim()
  if (!url) return
  if (!/^https?:\/\//i.test(url)) url = 'https://' + url
  fetchRecord(url)
}

/* ---------- 删除 ---------- */
export async function removeRecord(record) {
  const label = record.title || record.url
  if (!window.confirm(`确定删除「${label}」？`)) return
  try {
    const data = await apiDel('/api/record?url=' + encodeURIComponent(record.url))
    if (data.ok) {
      store.records = store.records.filter((r) => r.url !== record.url)
      update({ records: store.records.slice() })
      syncSelectBar()
      setStatus(`已删除：${label}`)
    } else {
      setStatus('删除失败：' + data.error, 'err')
    }
  } catch (e) {
    setStatus('删除失败：' + e.message, 'err')
  }
}

/* ---------- 域名子页路由 ---------- */
const HASH_PREFIX = '#/domain/'
export function goToDomain(domain) {
  if (!domain) return
  const next = HASH_PREFIX + encodeURIComponent(domain)
  if (location.hash === next) return
  location.hash = next
}
export function leaveDomainPage() {
  if (!store.domainPage) return
  update({ domainPage: null })
  if (location.hash) location.hash = ''
  update({}) // render
}
export function syncDomainRoute() {
  const m = /^#\/domain\/(.+)$/.exec(location.hash || '')
  let next = m ? decodeURIComponent(m[1]) : null
  if (next && (next === '__other__' || next.length > 100)) next = null
  if (next === store.domainPage) return false
  const entering = !!next && !store.domainPage
  update({ domainPage: next })
  if (entering) applyPrefsTag()
  return true
}
function readPrefs(cfg) {
  const p = (cfg && cfg.prefs) || {}
  return {
    sort: p.sort || 'default',
    fields: Array.isArray(p.fields) && p.fields.length ? p.fields : ['tags', 'details'],
    tag: p.tag || '',
    view: p.view === 'list' ? 'list' : 'card',
  }
}
export function applyPrefsTag() {
  if (!store.domainPage) return
  const prefs = readPrefs(store.domainConfig.get(store.domainPage))
  update({ selectedTag: prefs.tag || null })
}
function savePrefs(domain, prefs) {
  const cfg = store.domainConfig.get(domain) || {}
  cfg.prefs = prefs
  store.domainConfig.set(domain, cfg)
  update({ domainConfig: store.domainConfig })
  apiPatch('/api/domain', { name: domain, prefs }).catch(() => {})
}
export { readPrefs, savePrefs }

/* ---------- 多选 ---------- */
export function syncSelectBar() {
  const known = new Set(store.records.map((r) => r.url))
  for (const u of [...store.selectedUrls]) {
    if (!known.has(u)) store.selectedUrls.delete(u)
  }
  update({ selectedUrls: store.selectedUrls })
}
export function toggleSelect(url, on) {
  if (on) store.selectedUrls.add(url)
  else store.selectedUrls.delete(url)
  update({ selectedUrls: store.selectedUrls })
}
export function selectVisible() {
  for (const r of getVisibleRecords()) store.selectedUrls.add(r.url)
  update({ selectedUrls: store.selectedUrls })
}
export function clearSelection() {
  store.selectedUrls.clear()
  update({ selectedUrls: store.selectedUrls })
}
export function invertSelection() {
  for (const r of getVisibleRecords()) {
    if (store.selectedUrls.has(r.url)) store.selectedUrls.delete(r.url)
    else store.selectedUrls.add(r.url)
  }
  update({ selectedUrls: store.selectedUrls })
}

/* ---------- 补抓引擎 ---------- */
const PAUSE_KEY = 'almond.backfill.paused'
const WORKERS = 5

function loadPaused() {
  try {
    return localStorage.getItem(PAUSE_KEY) === '1'
  } catch (e) {
    return false
  }
}
function savePaused(on) {
  try {
    if (on) localStorage.setItem(PAUSE_KEY, '1')
    else localStorage.removeItem(PAUSE_KEY)
  } catch (e) {}
}
function autoRulesOk(url) {
  const cfg = store.domainConfig.get(rootDomain(hostOf(url)))
  if (cfg && cfg.auto_fetch === false) return false
  return true
}
function autoFetchEligible(url) {
  if (!autoRulesOk(url)) return false
  const cfg = store.domainConfig.get(rootDomain(hostOf(url)))
  return !!(cfg && cfg.tested && cfg.tested.plan === true)
}
const confirmGaveUp = new Set()
function needsConfirmDomain(url) {
  if (!autoRulesOk(url)) return null
  const d = rootDomain(hostOf(url))
  if (confirmGaveUp.has(d)) return null
  const cfg = store.domainConfig.get(d)
  if (cfg && cfg.tested && cfg.tested.plan === true) return null
  if (cfg && cfg.tested && cfg.tested.confirm) return null
  return d
}
function bUpdate() {
  update({ backfill: store.backfill })
}

async function confirmDomains(names) {
  const todo = names.filter((d) => !confirming.has(d))
  for (const d of todo) confirming.add(d)
  if (!todo.length) return false
  setStatus(`正在确认 ${todo.length} 个域名的抓取方案（挑真实网址试抓）…`, 'busy')
  try {
    const data = await apiPost('/api/confirm', { domains: todo, proxy: store.globalProxy })
    if (!data || data.stale) return false
    for (const t of data.transient || []) confirmGaveUp.add(t.domain)
    await loadDomainConfig()
    await loadRecords()
    const parts = []
    if ((data.confirmed || []).length) parts.push(`新确认可用 ${data.confirmed.length} 个`)
    if ((data.ready || []).length) parts.push(`已有方案 ${data.ready.length} 个`)
    if ((data.failed || []).length) parts.push(`无方案 ${data.failed.length} 个`)
    if ((data.transient || []).length) parts.push(`临时失败 ${data.transient.length} 个`)
    if (parts.length) setStatus(`方案确认完成：${parts.join('，')}`, (data.failed || []).length ? 'err' : undefined)
    return true
  } catch (e) {
    setStatus('方案确认失败：' + e.message, 'err')
    return false
  } finally {
    for (const d of todo) confirming.delete(d)
  }
}
const confirming = new Set()

export function resetBackfill() {
  const b = store.backfill
  b.gen++
  b.urls.length = 0
  b.inflight.clear()
  b.forceUrls.clear()
  b.forceRun = false
  b.total = b.ok = b.fail = b.skip = 0
  b.note = ''
  b.running = false
  clearTimeout(pollTimer)
  bUpdate()
}

export function enqueueBackfill(urls, note, explicit) {
  const b = store.backfill
  let pool = urls
  if (!explicit) {
    const unknown = new Set()
    for (const u of urls) {
      const d = needsConfirmDomain(u)
      if (d) unknown.add(d)
    }
    if (unknown.size) {
      const gen = b.gen
      confirmDomains([...unknown]).then((ok) => {
        if (!ok || gen !== b.gen) return
        const rest = urls.filter((u) => !needsConfirmDomain(u))
        if (rest.length) enqueueBackfill(rest, note)
      })
      pool = urls.filter((u) => !unknown.has(rootDomain(hostOf(u))))
    }
    pool = pool.filter(autoFetchEligible)
  }
  if (explicit) {
    for (const u of pool) if (u) b.forceUrls.add(u)
    b.forceRun = true
    if (b.paused) {
      b.paused = false
      savePaused(false)
    }
  }
  const queued = new Set(b.urls)
  const add = pool.filter((u) => u && !queued.has(u) && !b.inflight.has(u))
  if (!add.length) {
    if (note) setStatus(note)
    bUpdate()
    if (explicit && b.urls.length && !b.running) runBackfill()
    return
  }
  if (!b.running && !b.urls.length) {
    b.total = b.ok = b.fail = b.skip = 0
    b.note = ''
  }
  if (note) b.note = note
  b.urls.push(...add)
  b.total += add.length
  if (b.paused) {
    setStatus(`${note ? note + '；' : ''}补抓已暂停（待抓 ${b.urls.length} 条）`)
    bUpdate()
    return
  }
  setStatus(`${note ? note + '；' : ''}补抓：待抓 ${b.urls.length} 条（${WORKERS} 并发）`, 'busy')
  bUpdate()
  runBackfill()
}

export function startBackfill(note) {
  const b = store.backfill
  b.paused = loadPaused()
  const pending = store.records.filter((r) => !r.fetched).map((r) => r.url)
  enqueueBackfill(pending, note)
  startPolling()
}

export function forceBackfill() {
  setStatus('强制补抓：读取待抓与失败记录…', 'busy')
  loadRecords().then(() => {
    const failedUrls = store.records.filter((r) => r.fetched && !r.success).map((r) => r.url)
    const targets = store.records.filter((r) => !r.fetched || !r.success).map((r) => r.url)
    if (!targets.length) {
      setStatus('没有需要补抓的记录')
      bUpdate()
      return
    }
    for (const u of failedUrls) store.backfill.forceUrls.add(u)
    store.backfill.forceRun = true
    enqueueBackfill(targets, `强制补抓 ${targets.length} 条${failedUrls.length ? `（含失败重试 ${failedUrls.length} 条）` : ''}`)
  })
}

export function handleBackfillClick() {
  const b = store.backfill
  if (b.running || b.urls.length || b.paused) {
    toggleBackfill()
    return
  }
  forceBackfill()
}

export function toggleBackfill() {
  const b = store.backfill
  b.paused = !b.paused
  savePaused(b.paused)
  if (b.paused) {
    setStatus(`补抓已暂停（剩余 ${b.urls.length} 条）`)
    bUpdate()
    return
  }
  bUpdate()
  runBackfill()
}

const POLL_MS = 10000
let pollTimer = 0
let polling = false
function schedulePoll() {
  clearTimeout(pollTimer)
  pollTimer = setTimeout(pollPending, POLL_MS)
}
function pollPending() {
  const gen = store.backfill.gen
  const ctrl = new AbortController()
  const timer = setTimeout(() => ctrl.abort(), 8000)
  apiGet('/api/records/pending', { signal: ctrl.signal })
    .then((data) => {
      clearTimeout(timer)
      if (gen !== store.backfill.gen) return
      const urls = (data && data.urls) || []
      const known = new Set(store.records.map((r) => r.url))
      const ready = urls.some((u) => !known.has(u)) ? loadRecords() : Promise.resolve()
      return ready.then(() => {
        if (gen === store.backfill.gen) enqueueBackfill(urls)
      })
    })
    .then(() => {
      if (gen === store.backfill.gen) schedulePoll()
    })
    .catch(() => {
      clearTimeout(timer)
      if (gen === store.backfill.gen) schedulePoll()
    })
}
function startPolling() {
  if (!polling) {
    polling = true
    document.addEventListener('visibilitychange', () => {
      if (!document.hidden) {
        clearTimeout(pollTimer)
        pollPending()
      }
    })
  }
  schedulePoll()
}

async function runBackfill() {
  const b = store.backfill
  if (b.running || b.paused) return
  if (!b.urls.length) {
    bUpdate()
    return
  }
  const gen = b.gen
  b.running = true
  if (!store.proxyManual) {
    try {
      await detectProxy({ fill: true, announce: false })
    } catch (e) {}
  }
  if (gen !== b.gen) return

  const worker = async () => {
    while (gen === b.gen && b.urls.length && !b.paused) {
      const url = b.urls.shift()
      const rec = store.records.find((r) => r.url === url)
      const forced = b.forceUrls.delete(url)
      if (!rec || (rec.fetched && !forced) || (!forced && !autoFetchEligible(url))) {
        b.skip++
        bUpdate()
        continue
      }
      setStatus(`补抓 ${b.total - b.urls.length}/${b.total}（成功 ${b.ok}，失败 ${b.fail}）`, 'busy')
      bUpdate()
      b.inflight.add(url)
      try {
        const data = await fetchJob('/api/fetch', { url, proxy: store.globalProxy })
        if (gen !== b.gen) {
          /* 切存储：作废 */
        } else if (data.stale) {
          /* 服务端纪元兜底 */
        } else if (data.ok) {
          upsert(data.record)
          const fetched = data.success === undefined ? !!data.record.success : data.success
          if (fetched) b.ok++
          else b.fail++
          if (store.detailTarget && store.detailTarget.url === data.record.url) update({ detailTarget: data.record })
        } else b.fail++
      } catch (e) {
        if (gen === b.gen && (!e || e.name !== 'AbortError')) b.fail++
      } finally {
        b.inflight.delete(url)
      }
      if (gen !== b.gen) return
      bUpdate()
    }
  }
  Promise.all(Array.from({ length: WORKERS }, () => worker())).then(() => {
    if (gen !== b.gen) return
    b.running = false
    if (b.urls.length) {
      if (b.paused) {
        setStatus(`补抓已暂停（剩余 ${b.urls.length} 条）`)
        bUpdate()
        return
      }
      runBackfill()
      return
    }
    if (b.paused) {
      b.paused = false
      savePaused(false)
    }
    const forcedRun = b.forceRun
    b.forceRun = false
    b.forceUrls.clear()
    const kind = forcedRun ? '强制补抓' : '自动补抓'
    const prefix = b.note ? b.note + '；' : ''
    if (!b.ok && !b.fail) {
      setStatus(`${prefix}${kind}：${b.total} 条此前都已抓过，无需补抓`)
      bUpdate()
      return
    }
    setStatus(
      `${prefix}${kind}完成：成功 ${b.ok}，失败 ${b.fail}` +
        `${b.skip ? `，跳过 ${b.skip}` : ''}（共 ${b.total} 条）`,
      b.ok ? '' : 'err',
    )
    bUpdate()
  })
}

/* ---------- 代理 ---------- */
export async function detectProxy({ fill = false, announce = true } = {}) {
  try {
    const data = await apiPost('/api/proxy-detect', {})
    const found = data.proxy || ''
    if (found) {
      if (fill) {
        store.globalProxy = found
        store.proxyManual = false
        update({ globalProxy: found, proxyManual: false })
      }
      if (announce) setStatus(`检测到本地代理：${found}`)
    } else if (announce) {
      setStatus('7889-7899 区间未发现本地代理', 'err')
    }
    return found
  } catch (e) {
    if (announce) setStatus('代理探测失败：' + e.message, 'err')
    return ''
  }
}

export async function saveGlobalProxy(val) {
  store.globalProxy = val
  store.proxyManual = true
  update({ globalProxy: val, proxyManual: true })
  setStatus(
    val ? `全局代理已设为 ${val}（自下一条抓取起生效）` : '已清除全局代理，改走直连（自动探测不再覆盖）',
  )
}

export function startProxyPoll() {
  setInterval(async () => {
    try {
      const data = await apiGet('/api/proxy-current')
      const found = data.proxy || ''
      if (found && found !== store.globalProxy && !store.proxyManual) {
        store.globalProxy = found
        update({ globalProxy: found })
        setStatus(`代理已自动更新为 ${found}`)
      }
    } catch (e) {}
  }, 30000)
}

export async function addRule(pattern, needProxy) {
  if (!pattern) return
  await apiPost('/api/proxy-rule', { pattern, need_proxy: needProxy })
  loadProxyDomains()
  setStatus(`已添加规则：${pattern} → ${needProxy ? '用代理' : '直连'}`)
}
export async function removeRule(id) {
  await apiDel(`/api/proxy-rule?id=${id}`)
  loadProxyDomains()
}
export async function patchNeedProxy(name, needProxy) {
  try {
    const data = await apiPatch('/api/domain', { name, need_proxy: needProxy })
    if (!data.ok) {
      setStatus('更新失败：' + data.error, 'err')
      return
    }
    const label = needProxy === null ? '跟随全局（已取消指定）' : needProxy ? '用代理' : '直连'
    setStatus(`已指定 ${name} → ${label}`)
    loadProxyDomains()
  } catch (e) {
    setStatus('域名更新失败：' + e.message, 'err')
  }
}

/* ---------- 域名设置保存（供 DomainSettings 复用） ---------- */
export async function saveDomainSettings(domain, payload) {
  const data = await apiPatch('/api/domain', payload)
  if (data.ok) await loadDomainConfig()
  return data
}

/* ---------- 代理面板：方案刷新 / 单域名重测 ---------- */
export async function refreshTests() {
  const wasPaused = store.backfill.paused
  store.backfill.paused = true
  abortAll()
  try {
    const data = await apiGet('/api/domains')
    const todo = (data.domains || [])
      .filter((d) => {
        const t = d.tested
        if (!t) return true
        if (t.confirm && t.confirm.ok === false) return true
        if (t.confirm && t.plan !== true) return true
        return false
      })
      .map((d) => d.name)
    if (!todo.length) {
      setStatus('所有域名均已确认且有可用方案，无需刷新')
      return
    }
    setStatus(`正在重新确认 ${todo.length} 个域名的抓取方案…`, 'busy')
    const result = await apiPost('/api/confirm', { domains: todo, proxy: store.globalProxy, force: true })
    if (result.stale) {
      setStatus('存储已切换，刷新作废')
      return
    }
    await loadDomainConfig()
    const parts = []
    if ((result.confirmed || []).length) parts.push(`新确认 ${result.confirmed.length} 个`)
    if ((result.ready || []).length) parts.push(`已有方案 ${result.ready.length} 个`)
    if ((result.failed || []).length) parts.push(`无方案 ${result.failed.length} 个`)
    if ((result.transient || []).length) parts.push(`临时失败 ${result.transient.length} 个`)
    const skipped = result.skipped || []
    if (skipped.length) {
      const shown = skipped.slice(0, 3).map((sk) => `${sk.domain}：${sk.reason}`).join('；')
      parts.push(`跳过 ${skipped.length} 个（${shown}${skipped.length > 3 ? ' 等' : ''}）`)
    }
    setStatus(parts.length ? `刷新完成：${parts.join('，')}` : '刷新完成')
  } catch (e) {
    setStatus('刷新失败：' + e.message, 'err')
  } finally {
    store.backfill.paused = wasPaused
    startBackfill()
  }
}

export async function retryDomain(name) {
  const wasPaused = store.backfill.paused
  store.backfill.paused = true
  abortAll()
  try {
    const result = await apiPost('/api/confirm', { domains: [name], proxy: store.globalProxy, force: true })
    if (result.stale) {
      setStatus('存储已切换，重测作废')
      return
    }
    await loadDomainConfig()
    const parts = []
    if ((result.confirmed || []).length) parts.push(`新确认 ${result.confirmed.length} 个`)
    if ((result.ready || []).length) parts.push(`已有方案 ${result.ready.length} 个`)
    if ((result.failed || []).length) parts.push(`无方案 ${result.failed.length} 个`)
    if ((result.transient || []).length) parts.push(`临时失败 ${result.transient.length} 个`)
    const skipped = result.skipped || []
    if (skipped.length) parts.push(`跳过（${skipped[0].domain}：${skipped[0].reason}）`)
    setStatus(`${name} 重测完成：${parts.join('，') || '无变化'}`)
  } catch (e) {
    setStatus(`重测 ${name} 失败：` + e.message, 'err')
  } finally {
    store.backfill.paused = wasPaused
    startBackfill()
  }
}

/* ---------- 标签 ---------- */
export async function loadTags() {
  const data = await apiGet('/api/tags')
  return data.tags || []
}
export async function createTag(name) {
  if (!name) return
  await apiPost('/api/tag', { name })
}
export async function tagRecord(url, tagId) {
  await apiPost('/api/record/tag', { url, tag_id: tagId })
}
export async function untagRecord(url, tagId) {
  await apiDel(`/api/record/tag?url=${encodeURIComponent(url)}&tag_id=${tagId}`)
}
export async function renameRecord(url, title) {
  const data = await apiPatch('/api/record', { url, title })
  if (data.ok) {
    const rec = store.records.find((r) => r.url === url)
    if (rec) {
      rec.title = title
      update({ records: store.records.slice() })
    }
  }
  return data
}

/* ---------- 存储目录切换 ---------- */
export const fsState = { path: '', parent: '', storage: '' }

export async function loadStorageDir() {
  const d = await apiGet('/api/storage-dir')
  fsState.storage = d.path
  return d
}
export async function loadFs(path) {
  const d = await apiGet('/api/fs/list?path=' + encodeURIComponent(path || ''))
  return d
}
export async function dropHistory(path) {
  return apiDel('/api/storage-history?path=' + encodeURIComponent(path))
}

export async function saveDbPath(path) {
  if (!path) {
    setStatus('请先选择或输入存储目录', 'err')
    return
  }
  setStatus('正在切换存储目录：中止旧任务…', 'busy')
  abortAll()
  resetBackfill()
  store.records = []
  store.selectedUrls.clear()
  store.filter = ''
  store.selectedDomain = null
  store.selectedTag = null
  store.domainPage = null
  store.domainConfig = new Map()
  store.proxyDomains = new Set()
  update({
    records: [], selectedUrls: new Set(), filter: '',
    selectedDomain: null, selectedTag: null, domainPage: null,
    domainConfig: new Map(), proxyDomains: new Set(),
  })
  try {
    const data = await apiPost('/api/storage-dir', { path })
    if (!data.ok) {
      setStatus('切换失败：' + (data.error || '未知错误'), 'err')
      return
    }
    fsState.storage = data.path
    if (typeof data.epoch === 'number') store.storageEpoch = data.epoch
    update({ storageEpoch: store.storageEpoch })
    setStatus('正在切换存储目录：载入新目录…', 'busy')
    await reloadAll()
    startBackfill('存储已切换，扫描新目录')
    setStatus(
      `存储目录已切换到 ${data.path}` +
        (data.db_used_existing ? '（使用该目录现有数据库）' : '（新目录，从零开始）') +
        `；旧任务已中止、状态已重置 · 共 ${data.records} 条`,
    )
  } catch (e) {
    setStatus('切换失败：' + e.message, 'err')
  }
}
