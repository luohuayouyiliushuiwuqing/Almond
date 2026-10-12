/* 网络动作：添加 URL、抓取/重新抓取单条。
   传输层（epoch 注入 / 中止登记 / 轮询）在 api.js，这里只做业务编排。 */
import { fetchJob } from "./api.js";
import { emit } from "./bus.js";
import { openDetail } from "./detail.js";
import { upsert } from "./records.js";
import { $, setStatus, state } from "./state.js";

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
    if (data.aborted) {
      setStatus("抓取已中止（存储目录已切换）");
      return null;
    }
    if (!data.ok) {
      setStatus("抓取失败：" + data.error, "err");
      return null;
    }
    /* 已在看板上的记录就地补丁；**新记录**（粘贴 URL 添加）updateCard 找不到卡
       是 no-op（懒建语义），必须补一次重排——否则状态栏说「已抓取」但板上无卡 */
    const existed = state.records.some((r) => r.url === data.record.url);
    upsert(data.record);
    if (existed) emit("updateCard", data.record);
    else emit("render");
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
