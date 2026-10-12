/* 数据层：加载、合并、筛选可见记录、代理域名集合、删除。 */
import { apiDel, apiGet } from "./api.js";
import { emit } from "./bus.js";
import { MIN_GROUP, hostOf, rootDomain, setStatus, state } from "./state.js";

/* ---------- 数据加载与渲染 ---------- */

export async function loadRecords() {
  try {
    const data = await apiGet("/api/records");
    if (data.error) { // 服务端异常（如存储目录不存在）：原样提示
      setStatus("加载失败：" + data.error, "err");
      return;
    }
    state.records = data.records || [];
    if (typeof data.epoch === "number") state.storageEpoch = data.epoch;   // 抓取请求要带的纪元
    emit("render");
    setStatus(`共 ${state.records.length} 条`);
  } catch (e) {
    setStatus("加载失败：" + e.message, "err");
  }
}

export function upsert(record) {
  const i = state.records.findIndex((r) => r.url === record.url);
  /* 原地合并，不是换对象：render() 把同一批引用存进了懒建分组的 grid.__recs，
     详情弹层、卡片闭包也各自持着旧引用。换对象的话，补抓时卡片还没建
     （updateCard 找不到就 return），等滚动到那儿用的还是旧数据——
     灰点、没标题，得等下一次全量 render 才纠正。API 返回的是完整记录，
     不存在「缺键留旧值」的问题。 */
  if (i >= 0) Object.assign(state.records[i], record);
  else state.records.unshift(record);
}

export function matchesFilter(record) {
  if (!state.filter) return true;
  const hay = `${record.title || ""} ${record.url}`.toLowerCase();
  return hay.includes(state.filter);
}

/* 标签筛选单独拎出来：域名子页只按「域名 + 搜索 + 标签」取数，
   不走 getVisibleRecords() 的域名分支（那是筛选口径，会跟子页域名打架） */
export function matchesTag(record) {
  if (!state.selectedTag) return true;
  if (state.selectedTag === "__untagged__") return !record.tags || !record.tags.length;
  return !!record.tags && record.tags.some((t) => t.name === state.selectedTag);
}

/* ---------- 当前可见记录（搜索 + 域名 + 标签过滤，render 与导出共用） ---------- */

export function getVisibleRecords() {
  let visible = state.records.filter(matchesFilter);
  /* 域名子页优先：子页口径 = 「这个域名 + 搜索 + 标签」。
     放在这里而不是子页自己过滤，全选/反选/导出（都用本函数）的范围就自动正确 */
  if (state.domainPage) {
    visible = visible.filter((r) => r.domain === state.domainPage);
  } else if (state.selectedDomain === "__other__") {
    /* 零散域名合并项：按全量计数 < MIN_GROUP 的域名成员过滤 */
    const counts = new Map();
    for (const r of state.records) {
      const d = rootDomain(hostOf(r.url)) || "unknown";
      counts.set(d, (counts.get(d) || 0) + 1);
    }
    const minorSet = new Set(
      [...counts.entries()].filter(([, c]) => c < MIN_GROUP).map(([d]) => d)
    );
    visible = visible.filter(r => minorSet.has(rootDomain(hostOf(r.url)) || "unknown"));
  } else if (state.selectedDomain) {
    visible = visible.filter(r => rootDomain(hostOf(r.url)) === state.selectedDomain);
  }
  visible = visible.filter(matchesTag);
  return visible;
}

/* ---------- 走代理域名（侧边栏标记数据源） ---------- */

export async function loadProxyDomains() {
  try {
    const data = await apiGet("/api/proxy-domains");
    state.proxyDomains = new Set(data.domains || []);
  } catch (e) {
    state.proxyDomains = new Set();
  }
  emit("render");
}

/* 域名配置缓存（GET /api/domains → state.domainConfig）：
   补抓按它过滤 auto_fetch=0 的域名，域名子页的域头/设置也从它读。
   拿不到就保留上一份——网络抖一下不该让补抓或页面停摆 */
export async function loadDomainConfig() {
  try {
    const data = await apiGet("/api/domains");
    state.domainConfig = new Map((data.domains || []).map((d) => [d.name, d]));
  } catch (e) {
    /* 保持现状 */
  }
}

/* 改完域名设置/域名重置/切库后的统一三刷（原先 4 处各写各的） */
export async function reloadAll() {
  await Promise.all([loadRecords(), loadDomainConfig(), loadProxyDomains()]);
}

/* ---------- 删除 ---------- */

export async function removeRecord(record) {
  const label = record.title || record.url;
  if (!confirm(`确定删除「${label}」？`)) return;
  try {
    const data = await apiDel("/api/record?url=" + encodeURIComponent(record.url));
    if (data.ok) {
      state.records = state.records.filter((r) => r.url !== record.url);
      emit("render");
      setStatus(`已删除：${label}`);
    } else {
      setStatus("删除失败：" + data.error, "err");
    }
  } catch (e) {
    setStatus("删除失败：" + e.message, "err");
  }
}
