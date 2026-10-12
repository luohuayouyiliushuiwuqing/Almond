/* 多选操作条：勾选的真源是 state.selectedUrls（url 集合），卡片上的复选框只是投影。
   作用域约定：全选 / 反选只作用于**当前可见**记录（getVisibleRecords，跟随搜索与筛选），
   标签 / 重新抓取 / 删除 / 导出作用于**整个选中集合**（与「已选 N 条」一致）。 */
import { apiDel } from "./api.js";
import { emit } from "./bus.js";
import { backfillState, enqueueBackfill } from "./backfill.js";
import { doExport } from "./io.js";
import { openTagBatch } from "./modals.js";
import { getVisibleRecords } from "./records.js";
import { $, setStatus, state } from "./state.js";

/* ---------- 状态 → DOM ---------- */

/* 只写 class / checked，绝不重建节点：选择类操作必须让 #board 的 childList
   变更保持 0（一旦触发 render() 整板重建，就是之前修过的那个闪烁坑） */
function paintSelection() {
  /* 选择器用 [data-url] 而不是 .card：看板卡片与域名子页的列表行都带 data-url，
     一套类名/委托把两个视图的多选一起覆盖（行上也用 .card-check） */
  for (const node of document.querySelectorAll("#board [data-url]")) {
    const on = state.selectedUrls.has(node.dataset.url);
    node.classList.toggle("selected", on);
    const cb = node.querySelector(".card-check");
    if (cb) cb.checked = on;
  }
}

/* 收口：剪枝 + 计数 + 显隐。由 render() 末尾与所有勾选动作调用。
   ⚠ 本函数**绝不可以**调 render()，否则 render ⇄ sync 死循环 */
export function syncSelectBar() {
  const bar = $("selBar");
  if (!bar) return;

  /* 剪枝：库里已经不存在的 url（单卡删除 / 批量删除 / 换存储目录后重载…）
     从集合里抹掉，否则计数只增不减。放在这里做，免得 records.js 反向依赖本模块 */
  const known = new Set(state.records.map((r) => r.url));
  for (const u of [...state.selectedUrls]) {
    if (!known.has(u)) state.selectedUrls.delete(u);
  }

  const n = state.selectedUrls.size;
  bar.hidden = n === 0;
  $("selCount").textContent = `已选 ${n} 条`;

  /* 全选/反选是「可见范围」操作，没有可见记录就没有可翻的东西；
     标签/重抓/删除/导出作用于整个集合，保持可用 */
  const noVisible = getVisibleRecords().length === 0;
  $("selAll").disabled = noVisible;
  $("selInvert").disabled = noVisible;
}

function toggleSelect(url, on) {
  if (on) state.selectedUrls.add(url);
  else state.selectedUrls.delete(url);
  paintSelection();
  syncSelectBar();
}

/* ---------- 选择动作（可见范围） ---------- */

function selectVisible() {
  for (const r of getVisibleRecords()) state.selectedUrls.add(r.url);
  paintSelection();
  syncSelectBar();
}

/* 全不选 = 清空整个集合（含被筛掉的）：留一半会让「已选 N 条」与看到的对不上 */
function clearSelection() {
  state.selectedUrls.clear();
  paintSelection();
  syncSelectBar();
}

/* 反选只翻**可见**记录的成员关系，筛掉的保持原样 */
function invertSelection() {
  for (const r of getVisibleRecords()) {
    if (state.selectedUrls.has(r.url)) state.selectedUrls.delete(r.url);
    else state.selectedUrls.add(r.url);
  }
  paintSelection();
  syncSelectBar();
}

/* ---------- 批量动作（整个集合） ---------- */

async function deleteSelected() {
  const urls = [...state.selectedUrls];
  if (!urls.length) return;
  if (!confirm(`确定删除选中的 ${urls.length} 条记录？`)) return;

  /* 在途抓取的先别删：正在跑的 POST /api/fetch 会在 DELETE 之后把记录
     upsert 写回来（僵尸复活）。这几条保持选中，等抓完再删 */
  const inflight = urls.filter((u) => backfillState.inflight.has(u));
  const targets = urls.filter((u) => !backfillState.inflight.has(u));
  if (!targets.length) {
    setStatus(`${inflight.length} 条正在抓取，抓完再删`, "busy");
    return;
  }

  const bar = $("selBar");
  const controls = bar.querySelectorAll("button, select");
  controls.forEach((b) => { b.disabled = true; });   // 防确认框期间的双击
  let ok = 0;
  let fail = 0;
  try {
    for (let i = 0; i < targets.length; i++) {
      setStatus(`删除中 ${i + 1}/${targets.length}…`, "busy");
      try {
        const data = await apiDel("/api/record?url=" + encodeURIComponent(targets[i]));
        if (data.ok) {
          state.records = state.records.filter((r) => r.url !== targets[i]);
          state.selectedUrls.delete(targets[i]);
          ok++;
        } else fail++;
      } catch {
        fail++;
      }
    }
  } finally {
    controls.forEach((b) => { b.disabled = false; });
  }

  /* 只 render 一次：逐条 render 会把整板重建 N 次（又闪又慢）。
     setStatus 必须在 render 之后——render 结尾会写「共 N 条…」，会把结果盖掉 */
  emit("render");
  const parts = [`已删除 ${ok} 条`];
  if (fail) parts.push(`失败 ${fail} 条`);
  if (inflight.length) parts.push(`${inflight.length} 条正在抓取，已跳过删除`);
  setStatus(parts.join("，"), fail ? "err" : "");
}

function refetchSelected() {
  const known = new Set(state.records.map((r) => r.url));
  const urls = [...state.selectedUrls].filter((u) => known.has(u));
  if (!urls.length) return;
  /* 第三参 explicit：入队前打 forceUrls 标记（放行「已抓成功」的记录）、
     forceRun 让补抓按钮显示「强制补抓」、暂停中自动恢复。
     选择随即清空——进度交给补抓按钮，避免重复入队 */
  enqueueBackfill(urls, `批量重新抓取 ${urls.length} 条`, true);
  state.selectedUrls.clear();
  paintSelection();
  syncSelectBar();
}

function exportSelected(format) {
  const known = new Set(state.records.map((r) => r.url));
  const records = [...state.selectedUrls]
    .filter((u) => known.has(u))
    .map((u) => state.records.find((r) => r.url === u));
  doExport(format, records);
}

/* ---------- 接线（main.js 的 init() 调一次） ---------- */

export function initSelectBar() {
  /* 复选框的 change 用事件委托挂在 #board 上：render() 只清 innerHTML，
     监听器一直在，懒建分组后来补出来的卡片不用重新绑（也省掉 cards → selectbar 这条边） */
  $("board").addEventListener("change", (e) => {
    const cb = e.target.closest(".card-check");
    const host = cb && cb.closest("[data-url]");   // 卡片或子页列表行
    if (!host) return;
    toggleSelect(host.dataset.url, cb.checked);
  });
  $("selAll").addEventListener("click", selectVisible);
  $("selNone").addEventListener("click", clearSelection);
  $("selInvert").addEventListener("click", invertSelection);
  $("selTag").addEventListener("click", () => openTagBatch([...state.selectedUrls]));
  $("selRefetch").addEventListener("click", refetchSelected);
  $("selDelete").addEventListener("click", deleteSelected);
  $("selExport").addEventListener("change", (e) => {
    const format = e.target.value;
    e.target.value = "";              // 复位，允许重复导出同格式（与工具栏 #exportSelect 一致）
    if (format) exportSelected(format);
  });
}
