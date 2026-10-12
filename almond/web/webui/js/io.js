/* 导入（两阶段：快照入库→自动补抓）与导出。补抓队列本身在 backfill.js。 */
import { apiPost } from "./api.js";
import { enqueueBackfill } from "./backfill.js";
import { getVisibleRecords, loadRecords } from "./records.js";
import { $, setStatus, state } from "./state.js";

/* ---------- 导出 ---------- */

function escHtml(s) {
  return String(s || "").replace(/&/g, "&amp;").replace(/</g, "&lt;")
    .replace(/>/g, "&gt;").replace(/"/g, "&quot;");
}

function downloadFile(fileName, content, mime) {
  const blob = new Blob([content], { type: mime });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = fileName;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(a.href);
}

function exportTxt(records) {
  const text = records.map(r => r.url).join("\n") + "\n";
  downloadFile("bookmarks.txt", text, "text/plain;charset=utf-8");
}

function exportHtml(records) {
  const now = Math.floor(Date.now() / 1000);
  const lines = records.map(r => {
    const tags = (r.tags || []).map(t => t.name).join(",");
    const tagAttr = tags ? ` TAGS="${escHtml(tags)}"` : "";
    return `    <DT><A HREF="${escHtml(r.url)}" ADD_DATE="${now}"${tagAttr}>${escHtml(r.title || r.url)}</A>`;
  });
  const html = [
    "<!DOCTYPE NETSCAPE-Bookmark-file-1>",
    '<META HTTP-EQUIV="Content-Type" CONTENT="text/html; charset=UTF-8">',
    "<TITLE>Bookmarks</TITLE>",
    "<H1>Bookmarks</H1>",
    "<DL><p>",
    ...lines,
    "</DL><p>",
    "",
  ].join("\n");
  downloadFile("bookmarks.html", html, "text/html;charset=utf-8");
}

/* records 可传入：工具栏导出「当前可见」（默认），多选操作条导出「选中集合」 */
export function doExport(format, records = getVisibleRecords()) {
  if (!records.length) { setStatus("没有可导出的记录", "err"); return; }
  if (format === "txt") exportTxt(records);
  else if (format === "html") exportHtml(records);
  setStatus(`已导出 ${records.length} 条为 ${format.toUpperCase()}`);
}

/* ---------- TXT 导入 ---------- */

/* 解析导入文件 → {isHtml, items}。
   HTML：读出 HREF + 标题 + 封面 + 标签，可完全离线快照导入。
   TXT ：只有 URL，无元数据可读，仍需走抓取流程。 */
function parseBookmarks(text, fileName) {
  const seen = new Set();
  const items = [];
  const push = (item) => {
    if (!/^https?:\/\//i.test(item.url) || seen.has(item.url)) return;
    seen.add(item.url);
    items.push(item);
  };

  if (/\.html?$/i.test(fileName)) {
    const doc = new DOMParser().parseFromString(text, "text/html");
    for (const a of doc.querySelectorAll("a[href]")) {
      push({
        url: a.href,
        title: (a.textContent || "").replace(/\s+/g, " ").trim(),
        thumbnail: a.getAttribute("data-cover") || "",
        favicon: "",
        tags: (a.getAttribute("tags") || "").split(",").map(s => s.trim()).filter(Boolean),
      });
    }
    return { isHtml: true, items };
  }

  for (const line of text.split(/\r?\n/)) push({ url: line.trim() });
  return { isHtml: false, items };
}

/* ---------- 导入：先把链接全部入库，再自动补抓 ---------- */
export async function importFromFile(file) {
  const text = await file.text();
  const { isHtml, items } = parseBookmarks(text, file.name);
  if (!items.length) { setStatus("文件中未找到 http 开头的 URL", "err"); return; }

  // 必须在入库前取快照，才能知道哪些是这次新增的
  const existing = new Set(state.records.map((r) => r.url));
  const fresh = items.filter((i) => !existing.has(i.url));

  $("importBtn").disabled = true;

  /* ── 阶段一：链接全部入库（快照，不联网，5000 条秒级）── */
  let data;
  try {
    setStatus(`导入链接中…（${items.length} 条，不联网）`, "busy");
    data = await apiPost("/api/records/quick", { items });
    if (!data.ok) { setStatus("导入失败：" + data.error, "err"); return; }
  } catch (e) {
    setStatus("导入失败：" + e.message, "err");
    return;
  } finally {
    $("importBtn").disabled = false;   // 链接一入库界面立刻可用，不等提取
  }

  await loadRecords();
  const parts = [`已入库 ${data.inserted} 条`];
  if (data.skipped) parts.push(`跳过 ${data.skipped} 条（已存在）`);
  if (isHtml) parts.push("标题/封面已从文件读取");

  /* ── 阶段二：自动补抓 ──
     两条路径进同一个后台队列（5 并发，不阻塞看板，工具栏可暂停）：
     TXT 不抓就没标题；HTML 标题/封面文件里有，但详情字段与失效封面仍要联网。
     本次没新增（全是已存在的）时队列自己不发状态，直接把入库结果放上去。 */
  enqueueBackfill(fresh.map((i) => i.url), parts.join("，"));
}
