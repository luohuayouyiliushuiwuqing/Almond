/* 详情弹层：按需抓取 + 字段渲染。 */
import { fetchRecord } from "./fetch.js";
import { $, el, state } from "./state.js";

/* ---------- 详情弹层（展示 .space-y-2 提取的标签值字段） ---------- */

export async function openDetail(record) {
  state.detailTarget = record;
  $("detailMask").hidden = false;
  if (record.fetched) { renderDetail(record); return; }

  /* 快照导入的记录没有 details：打开详情时才补抓（按需加载） */
  renderDetailLoading();
  await fetchRecord(record.url);   // 内部 upsert 会替换 state.records 里的对象
  const fresh = state.records.find(r => r.url === record.url) || record;
  if (state.detailTarget) {
    state.detailTarget = fresh;
    renderDetail(fresh);           // 不递归：抓取失败也只提示一次，再点才重试
  }
}

function renderDetailLoading() {
  const box = $("detailFields");
  box.innerHTML = "";
  box.appendChild(el("div", "detail-empty", "正在抓取详情…（首次需要数秒）"));
}

function renderDetail(record) {
  $("detailTitle").textContent = record.title || "（标题待补充）";
  $("detailDomain").textContent = record.url;
  const box = $("detailFields");
  box.innerHTML = "";

  if (!record.fetched) {
    box.appendChild(el("div", "detail-empty", "详情尚未抓取，点「重新抓取」或再次打开本弹层"));
    return;
  }
  if (!record.success) {
    box.appendChild(el("div", "detail-empty", "该记录抓取失败，点「重新抓取」再试"));
    return;
  }
  const details = record.details || [];
  if (!details.length) {
    box.appendChild(el("div", "detail-empty", "页面中未提取到 .space-y-2 详情字段，点「重新抓取」更新"));
    return;
  }
  for (const f of details) {
    const row = el("div", "detail-row");
    row.appendChild(el("span", "detail-label", f.label));
    const value = el("span", "detail-value");
    // 日期类字段直接显示纯文本（2026-09-15），不用徽标样式
    const dateText = f.datetime ? f.datetime.slice(0, 10) : "";
    if (f.links && f.links.length) {
      for (const l of f.links) {
        const a = document.createElement("a");
        a.textContent = l.text || l.href;
        a.href = l.href;
        a.target = "_blank";
        a.rel = "noopener";
        a.title = l.href;
        value.appendChild(a);
      }
      const rest = f.value || dateText;
      if (rest) value.appendChild(document.createTextNode(" " + rest));
    } else if (f.value) {
      value.appendChild(document.createTextNode(f.value));
    } else if (dateText) {
      value.appendChild(document.createTextNode(dateText));
    }
    row.appendChild(value);
    box.appendChild(row);
  }
}

export function closeDetail() {
  $("detailMask").hidden = true;
  state.detailTarget = null;
}
