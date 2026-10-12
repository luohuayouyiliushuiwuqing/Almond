/* 统一请求层：除 <img src="/api/img"> 外，fetch() 只允许出现在本文件。
   - 对象 body 自动注入 `epoch: state.storageEpoch`（服务端收包校验，切库旧请求作废）；
   - 在途请求登记进 controllers，切存储时 `abortAll()` 一次全中止；
   - 响应含 `proxy_swapped` 自动 `adoptProxy`（服务端顶替坏代理端口后前端跟上）；
   - `stale` 原样返回，调用方决定文案；AbortError 原样抛/标记返回。 */
import { adoptProxy, state } from "./state.js";

const controllers = new Set();

/** 中止全部在途请求。切存储编排调用（原 fetch.abortAllFetches）。 */
export function abortAll() {
  for (const c of controllers) c.abort();
  controllers.clear();
}

/** 唯一请求入口。返回解析后的 JSON 对象。 */
export async function request(method, path, body, opts = {}) {
  const ctrl = opts.signal ? null : new AbortController();
  if (ctrl) controllers.add(ctrl);
  const sig = opts.signal || (ctrl && ctrl.signal);
  const init = { method, signal: sig };
  if (body != null) {
    const payload = body && typeof body === "object" && state.storageEpoch != null
      ? { ...body, epoch: state.storageEpoch }
      : body;
    init.headers = { "Content-Type": "application/json" };
    init.body = JSON.stringify(payload);
  }
  try {
    const resp = await fetch(path, init);
    const data = await resp.json();
    if (data && data.proxy_swapped) adoptProxy(data.proxy_swapped);
    return data;
  } finally {
    if (ctrl) controllers.delete(ctrl);
  }
}

export function apiGet(path, opts) {
  return request("GET", path, null, opts);
}

export function apiPost(path, body, opts) {
  return request("POST", path, body, opts);
}

export function apiPatch(path, body, opts) {
  return request("PATCH", path, body, opts);
}

export function apiDel(path, opts) {
  return request("DELETE", path, null, opts);
}

/* —— 异步抓取轮询（避免长连接占满浏览器 6 连接上限）——

   旧版同步模式：POST /api/fetch → 后端等子进程完成 → 数秒~数分钟后返回结果。
   5 并发 × 长等待 → 浏览器 HTTP/1.1 的 6 连接全部占满 → 前端的
   GET /api/storage-dir、GET /api/proxy-detect 等全部排不上队 → 界面卡死。

   新版异步模式：POST /api/fetch → 后端立即返回 job_id →
   前端轮询 GET /api/fetch/status?job_id=N（500ms 间隔，首轮延迟 1s 给子进程启动时间）
   → 204 继续等 / 200 拿结果。连接占用从「分钟级阻塞」降到「秒级瞬间」。 */

const POLL_MS = 500;       // 轮询间隔（500ms，5 并发 → 每次最多 5 个 204 短请求）
const POLL_START = 1000;   // 首轮延迟（给子进程 1s 启动时间，避免无谓的立即查询）
const POLL_TIMEOUT = 300000; // 5 分钟超时（正常三级降级远快于此）

/**
 * 异步提交并轮询结果（供 backfill worker 和单条抓取共用）。
 * 返回结构与旧版 postFetch("/api/fetch", body) 完全一致：
 *  {ok:true, record, success} | {ok:true, stale:true,…} | {ok:false, error}
 *  | {ok:false, error:"AbortError", aborted:true}
 * signal 可选：传入 AbortController.signal，切存储时可中止轮询。
 */
export async function fetchJob(path, body, signal) {
  // ① 提交：后端立即返回 job_id（request 负责 epoch 注入与 proxy_swapped 采纳）
  const ctrl = signal ? null : new AbortController();
  if (ctrl) controllers.add(ctrl);
  const sig = signal || (ctrl && ctrl.signal);
  try {
    const sub = await request("POST", path, body, { signal: sig });
    if (sub.stale) return sub;           // 纪元校验失败，直接返回
    if (!sub.ok || !sub.job_id) return { ok: false, error: sub.error || "提交失败" };

    // ② 轮询：首轮延迟 1s 给子进程启动时间
    await new Promise((r) => setTimeout(r, POLL_START));
    if (sig && sig.aborted) return { ok: false, error: "AbortError", aborted: true };

    const deadline = Date.now() + POLL_TIMEOUT;
    const statusUrl = `/api/fetch/status?job_id=${sub.job_id}`;
    while (Date.now() < deadline) {
      if (sig && sig.aborted) return { ok: false, error: "AbortError", aborted: true };
      const poll = await fetch(statusUrl, { signal: sig });
      if (poll.status === 200) return await poll.json();   // 完成：拿到完整结果
      if (poll.status === 204) {                           // 还在跑：等待下次轮询
        await new Promise((r) => setTimeout(r, POLL_MS));
        continue;
      }
      // 404（任务丢失）或其他错误：立即报错
      return { ok: false, error: `轮询状态异常：HTTP ${poll.status}` };
    }
    return { ok: false, error: "抓取超时（5 分钟无响应）" };
  } catch (e) {
    if (e && e.name === "AbortError") return { ok: false, error: "AbortError", aborted: true };
    return { ok: false, error: "请求失败：" + e.message };
  } finally {
    if (ctrl) controllers.delete(ctrl);
  }
}
