/* 卡片驱动的弹窗：重命名、标签管理。 */
import { apiDel, apiGet, apiPatch, apiPost } from "./api.js";
import { emit } from "./bus.js";
import { loadRecords } from "./records.js";
import { $, el, setStatus, state } from "./state.js";

/* ---------- 重命名 ---------- */

export function openRename(record) {
  state.renameTarget = record;
  $("modalInput").value = record.title || "";
  $("modalMask").hidden = false;
  $("modalInput").focus();
}

export async function submitRename() {
  const record = state.renameTarget;
  if (!record) return;
  const title = $("modalInput").value.trim();
  $("modalMask").hidden = true;
  if (title === (record.title || "")) return;
  try {
    const data = await apiPatch("/api/record", { url: record.url, title });
    if (data.ok) {
      record.title = title;
      emit("updateCard", record);   // 改名只动标题文字，不整页重绘
      setStatus(`已重命名：${title || "(空)"}`);
    } else {
      setStatus("重命名失败：" + data.error, "err");
    }
  } catch (e) {
    setStatus("重命名失败：" + e.message, "err");
  } finally {
    state.renameTarget = null;
  }
}

/* ---------- 标签管理 ---------- */

export async function openTag(record) {
  state.tagTarget = record;
  state.tagTargets = null;   // 单条模式
  await renderTagModal();
  $("tagMask").hidden = false;
}

/* 多选操作条的「标签」：同一个弹窗，目标换成一批 url */
export async function openTagBatch(urls) {
  if (!urls || !urls.length) return;
  state.tagTarget = null;
  state.tagTargets = urls.slice();
  await renderTagModal();
  $("tagMask").hidden = false;
}

async function renderTagModal() {
  const box = $("tagList");
  box.innerHTML = "";
  /* 单条（tagTarget）与批量（tagTargets）共用一套渲染。
     active 判据 = 目标里**每一条**都带该标签；部分带 → 显示 +，点一下补齐 */
  const urls = state.tagTargets || (state.tagTarget ? [state.tagTarget.url] : []);
  const recs = urls.map((u) => state.records.find((r) => r.url === u)).filter(Boolean);
  if (!recs.length) { box.textContent = "没有目标记录"; return; }
  try {
    const data = await apiGet("/api/tags");
    const tags = data.tags || [];
    const hasAll = (id) => recs.every((r) => (r.tags || []).some((t) => t.id === id));
    for (const tag of tags) {
      const on = hasAll(tag.id);
      const item = el("div", "tag-item" + (on ? " active" : ""));
      item.innerHTML = `<span>${tag.name}</span><span class="tag-toggle">${on ? "✓" : "+"}</span>`;
      item.addEventListener("click", async () => {
        /* 对每条目标加/删同一个标签（单条时就是原来的那次请求） */
        for (const rec of recs) {
          if (on) {
            await apiDel(`/api/record/tag?url=${encodeURIComponent(rec.url)}&tag_id=${tag.id}`);
          } else {
            await apiPost("/api/record/tag", { url: rec.url, tag_id: tag.id });
          }
        }
        // 刷新记录数据（走 loadRecords：顺带更新 storageEpoch 并重绘）
        await loadRecords();
        renderTagModal();
      });
      box.appendChild(item);
    }
  } catch { box.textContent = "加载标签失败"; }
}

export async function addNewTag() {
  const name = $("tagInput").value.trim();
  if (!name) return;
  $("tagInput").value = "";
  await apiPost("/api/tag", { name });
  renderTagModal();
}
