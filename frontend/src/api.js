/* 统一请求层：对应原生前端的 api.js。
   对象 body 自动注入 epoch（store.storageEpoch，服务端切库旧请求作废）；
   proxy_swapped 自动 adoptProxy；响应里带 proxy_swapped 则回写全局代理。
   dev 阶段 /api 由 Vite 反代到 Python 后端，生产由同源 / 托管。 */

import { store } from './store.js'

const controllers = new Set()

export function abortAll() {
  for (const c of controllers) c.abort()
  controllers.clear()
}

/* —— 401 引导 ——
   服务端鉴权见 almond/webui/auth.py：浏览器靠 Cookie，Cookie 靠 ?token= 换。
   只提示一次：看板一进来就并发发十几个 GET，全部 401 时连弹十几个框没法收。 */
let prompting = false

function promptToken() {
  if (prompting) return
  prompting = true
  abortAll()
  const given = window.prompt('需要访问令牌：\n（也可以直接把 ?token=… 加在地址后面重新打开页面）')
  if (given && given.trim()) {
    location.href = '/api/login?token=' + encodeURIComponent(given.trim())
    return   // 整页跳走换 Cookie，不必复位 prompting
  }
  prompting = false
}

async function request(method, path, body, opts = {}) {
  const ctrl = opts.signal ? null : new AbortController()
  if (ctrl) controllers.add(ctrl)
  const sig = opts.signal || (ctrl && ctrl.signal)
  const init = { method, signal: sig }
  if (body != null) {
    const payload =
      body && typeof body === 'object' && store.storageEpoch != null
        ? { ...body, epoch: store.storageEpoch }
        : body
    init.headers = { 'Content-Type': 'application/json' }
    init.body = JSON.stringify(payload)
  }
  try {
    const resp = await fetch(path, init)
    if (resp.status === 401) {
      promptToken()
      return { ok: false, unauthorized: true, error: '未授权：缺少或错误的访问令牌' }
    }
    const data = await resp.json()
    if (data && data.proxy_swapped) adoptProxy(data.proxy_swapped)
    return data
  } finally {
    if (ctrl) controllers.delete(ctrl)
  }
}

/** 服务端把坏代理端口顶替为本机可用端口后，前端跟上新地址。 */
export function adoptProxy(addr) {
  if (!addr || addr === store.globalProxy) return
  store.globalProxy = addr
  store.proxyManual = false
  update({ globalProxy: addr, proxyManual: false })
}

export function apiGet(path, opts) {
  return request('GET', path, null, opts)
}
export function apiPost(path, body, opts) {
  return request('POST', path, body, opts)
}
export function apiPatch(path, body, opts) {
  return request('PATCH', path, body, opts)
}
export function apiDel(path, opts) {
  return request('DELETE', path, null, opts)
}

/* —— 异步抓取轮询 —— */
const POLL_MS = 500
const POLL_START = 1000
const POLL_TIMEOUT = 300000

export async function fetchJob(path, body, signal) {
  const ctrl = signal ? null : new AbortController()
  if (ctrl) controllers.add(ctrl)
  const sig = signal || (ctrl && ctrl.signal)
  try {
    const sub = await request('POST', path, body, { signal: sig })
    if (sub.stale) return sub
    if (!sub.ok || !sub.job_id) return { ok: false, error: sub.error || '提交失败' }
    await new Promise((r) => setTimeout(r, POLL_START))
    if (sig && sig.aborted) return { ok: false, error: 'AbortError', aborted: true }
    const deadline = Date.now() + POLL_TIMEOUT
    const statusUrl = `/api/fetch/status?job_id=${sub.job_id}`
    while (Date.now() < deadline) {
      if (sig && sig.aborted) return { ok: false, error: 'AbortError', aborted: true }
      const poll = await fetch(statusUrl, { signal: sig })
      if (poll.status === 200) return await poll.json()
      if (poll.status === 401) {
        promptToken()
        return { ok: false, unauthorized: true, error: '未授权：缺少或错误的访问令牌' }
      }
      if (poll.status === 204) {
        await new Promise((r) => setTimeout(r, POLL_MS))
        continue
      }
      return { ok: false, error: `轮询状态异常：HTTP ${poll.status}` }
    }
    return { ok: false, error: '抓取超时（5 分钟无响应）' }
  } catch (e) {
    if (e && e.name === 'AbortError') return { ok: false, error: 'AbortError', aborted: true }
    return { ok: false, error: '请求失败：' + e.message }
  } finally {
    if (ctrl) controllers.delete(ctrl)
  }
}

// update 由 store.js 提供；此处 import 会循环——用延迟引用避免。
let _update = null
export function _bindUpdate(fn) {
  _update = fn
}
function update(patch) {
  if (_update) _update(patch)
}
