/* 网络动作：添加 URL、抓取/重新抓取单条。 */
import { updateCard } from "./cards.js";
import { openDetail } from "./detail.js";
import { upsert } from "./records.js";
import { $, adoptProxy, setStatus, state } from "./state.js";

/* ---------- 在途请求管理（切存储时全部中止；服务端存储纪元作最终兜底） ---------- */

const controllers = new Set();

/** 中止所有在途抓取请求（切存储目录时调用）。 */
export function abortAllFetches() {
  for (const c of controllers) c.abort();
  controllers.clear();
}

/** 统一 POST：注册 AbortController，返回解析后的 JSON。
    请求体自动带上当前存储纪元（`state.storageEpoch`）：切库后才被服务端执行的
    旧轮请求会在入口被丢弃（stale），不会把旧目录的抓取结果写进新库。 */
export async function postFetch(path, body) {
  const ctrl = new AbortController();
  controllers.add(ctrl);
  const payload = state.storageEpoch != null && body && typeof body === "object"
    ? { ...body, epoch: state.storageEpoch }
    : body;
  try {
    const resp = await fetch(path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
      signal: ctrl.signal,
    });
    return await resp.json();
  } finally {
    controllers.delete(ctrl);
  }
}

/* ---------- 异步抓取轮询（避免长连接占满浏览器 6 连接上限） ----------

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
 * 返回与旧版 postFetch("/api/fetch", body) 完全一致的数据结构；
 * 出错/超时时返回 { ok: false, error: "..." }。
 * signal 可选：传入 AbortController.signal，切存储时可中止轮询。
 */
export async function fetchJob(path, body, signal) {
  // ① 提交：后端立即返回 job_id
  const ctrl = signal ? null : new AbortController();
  if (ctrl) controllers.add(ctrl);
  const sig = signal || (ctrl && ctrl.signal);
  const payload = state.storageEpoch != null && body && typeof body === "object"
    ? { ...body, epoch: state.storageEpoch }
    : body;
  try {
    const resp = await fetch(path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
      signal: sig,
    });
    const sub = await resp.json();
    if (sub.stale) return sub;           // 纪元校验失败，直接返回
    if (!sub.ok || !sub.job_id) return { ok: false, error: sub.error || "提交失败" };
    // 代理端口已被顶替：立即同步地址（不用等轮询结果）
    if (sub.proxy_swapped) adoptProxy(sub.proxy_swapped);

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

/* ---------- 抓取（添加 / 重新抓取） ---------- */

export async function fetchRecord(url) {
  const proxy = state.globalProxy;
  $("addBtn").disabled = true;
  setStatus(`正在抓取 ${url} …（三级降级，可能需要数秒到一两分钟）`, "busy");
  try {
    const data = await fetchJob("/api/fetch", { url, proxy });
    if (data.stale) {
      setStatus("存储已切换，本次抓取结果已丢弃");
      return null;
    }
    if (!data.ok) {
      setStatus("抓取失败：" + data.error, "err");
      return null;
    }
    upsert(data.record);
    updateCard(data.record);
    if (state.detailTarget && state.detailTarget.url === data.record.url) {
      openDetail(data.record);
    }
    const fetched = data.success === undefined ? !!data.record.success : data.success;
    setStatus(
      fetched
        ? `已抓取：${data.record.title || url}`
        : `三级抓取全部失败，已仅保存 URL：${url}`,
      fetched ? "" : "err"
    );
    return data.record;
  } catch (e) {
    if (e && e.name === "AbortError") {
      setStatus("抓取已中止（存储目录已切换）");
      return null;
    }
    setStatus("请求失败：" + e.message, "err");
    return null;
  } finally {
    $("addBtn").disabled = false;
  }
}

export function addUrl() {
  const input = $("urlInput");
  let url = input.value.trim();
  if (!url) return;
  if (!/^https?:\/\//i.test(url)) url = "https://" + url;
  input.value = "";
  fetchRecord(url);
}
