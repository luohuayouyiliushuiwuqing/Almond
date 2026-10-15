/* 弹窗逻辑：① 收藏当前页（注入读 og:image/favicon）② 批量收藏当前窗口标签。
   看板地址存 chrome.storage.local（不用 sync——不该把 127.0.0.1 同步到别的设备）。
   错误分三类提示：fetch 网络失败（看板没开）/ data.ok === false（400、500）/ 成功回显。 */
"use strict";

let boardUrl = DEFAULT_BOARD_URL;   // 来自 defaults.js；storage 读不到时兜底
let closeAfter = false;             // 收藏成功后是否关标签页（勾选即记住）
let token = "";                     // 看板开了鉴权才需要；走 X-Token 头，不用 Cookie

function $(id) {
  return document.getElementById(id);
}

function setStatus(msg, kind) {
  const el = $("status");
  el.textContent = msg;
  el.className = "status" + (kind ? " " + kind : "");
}

/* 只收 http(s)：chrome://、file://、chrome-extension://、商店页一律不算 */
function isHttpUrl(u) {
  return /^https?:\/\//.test(u || "");
}

/* 本地回环不收：看板自己就跑在 127.0.0.1:4000，收藏自己没有意义 */
function isLoopback(u) {
  try {
    const h = new URL(u).hostname;
    return h === "localhost" || h === "[::1]" || /^127\./.test(h);
  } catch {
    return false;
  }
}

/* 收藏资格：先过 http(s)（历史/设置/插件等系统页在此出局），再排回环 */
function isCollectible(u) {
  return isHttpUrl(u) && !isLoopback(u);
}

async function loadSettings() {
  const data = await chrome.storage.local.get(["boardUrl", "closeAfter", "token"]);
  const saved = (data.boardUrl || "").trim();
  boardUrl = saved || DEFAULT_BOARD_URL;
  $("boardUrl").value = boardUrl;
  closeAfter = !!data.closeAfter;
  $("closeAfter").checked = closeAfter;
  token = (data.token || "").trim();
  $("token").value = token;
}

/* 单次 POST、不分片（300 条约 60~150KB，服务端一个事务 <10ms）；
   空列表不发——服务端会 400「缺少 items」，调用方先拦 */
async function postItems(items) {
  const headers = { "Content-Type": "application/json" };
  // Cookie 在这里带不动：popup 的源是 chrome-extension://，对看板地址属跨站请求，
  // 而会话 Cookie 是 SameSite=Lax（跨站不发），只有显式头能过鉴权（见 almond/webui/auth.py）
  if (token) headers["X-Token"] = token;
  const resp = await fetch(`${boardUrl}/api/records/quick`, {
    method: "POST",
    headers,
    body: JSON.stringify({ items }),
  });
  return resp.json();
}

async function submit(items) {
  if (!items.length) {
    setStatus("没有可收藏的标签页", "err");
    return false;
  }
  setStatus(`提交中…（${items.length} 条）`, "busy");
  let data;
  try {
    data = await postItems(items);
  } catch (e) {
    setStatus("连不上看板：确认服务已启动、地址正确", "err");
    return false;
  }
  if (!data.ok) {
    setStatus(
      data.unauthorized
        ? "鉴权失败：看板启用了 token，请在下方「访问令牌」里填上"
        : "看板返回错误：" + (data.error || `HTTP ${data.status || "?"}`),
      "err"
    );
    return false;
  }
  setStatus(`新增 ${data.inserted}，跳过 ${data.skipped}`, "ok");
  return true;
}

/* ---------- 收藏当前页 ---------- */

/* 在点击那一刻注入：自包含（函数体被序列化，引不到 popup 的闭包），
   host_permissions 已保证注入可用，activeTab 只是兜底。返回值取 results[0]?.result */
function extractMeta() {
  const abs = (v) => {
    if (!v) return "";
    try {
      const u = new URL(v, location.href);
      return /^https?:\/\//.test(u.href) ? u.href : "";
    } catch {
      return "";
    }
  };
  const og =
    document.querySelector('meta[property="og:image"]')?.content ||
    document.querySelector('meta[name="twitter:image"]')?.content ||
    "";
  const icon = document.querySelector('link[rel~="icon"]')?.href || "";
  return { thumbnail: abs(og), favicon: abs(icon) };
}

async function saveCurrent() {
  const btn = $("saveCurrent");
  btn.disabled = true;
  try {
    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
    if (!tab || !isHttpUrl(tab.url)) {
      setStatus("当前页不是普通网页，无法收藏", "err");
      return;
    }
    if (isLoopback(tab.url)) {
      setStatus("本地回环页（127.0.0.1 / localhost）跳过，不收藏", "err");
      return;
    }
    let extracted = { thumbnail: "", favicon: "" };
    let degraded = false;
    try {
      const results = await chrome.scripting.executeScript({
        target: { tabId: tab.id },
        func: extractMeta,
      });
      extracted = (results && results[0] && results[0].result) || extracted;
    } catch (e) {
      // PDF 阅读器、应用商店等注入失败 → 降级成只有 title + favicon 仍可收藏
      degraded = true;
    }
    const favicon =
      extracted.favicon || (isHttpUrl(tab.favIconUrl) ? tab.favIconUrl : "");
    const ok = await submit([
      {
        url: tab.url,
        title: tab.title || "",
        thumbnail: extracted.thumbnail || "",
        favicon,
        tags: [],
      },
    ]);
    if (ok && degraded) {
      setStatus("已收藏（读不到页面元数据，仅标题/图标）", "ok");
    }
    /* 关当前标签会让 popup 一起消失，必须放在最后一步 */
    if (ok && closeAfter) {
      await chrome.tabs.remove(tab.id);
    }
  } finally {
    btn.disabled = false;
  }
}

/* ---------- 批量收藏当前窗口标签 ---------- */

async function collectibleTabs() {
  const tabs = await chrome.tabs.query({ currentWindow: true });
  return tabs.filter((t) => isCollectible(t.url));
}

async function saveAll() {
  const btn = $("saveAll");
  btn.disabled = true;
  try {
    const tabs = await collectibleTabs();
    /* 只取 title + 过滤后的 favIconUrl——不注入（那要给每个标签页各注入一次），
       缺封面没关系，看板里点「详情 / 重新抓取」会补上 */
    const ok = await submit(
      tabs.map((t) => ({
        url: t.url,
        title: t.title || "",
        thumbnail: "",
        favicon: isHttpUrl(t.favIconUrl) ? t.favIconUrl : "",
        tags: [],
      }))
    );
    /* 全收成功才关；关光后窗口若空会连 popup 一起关掉，同样是最后一步 */
    if (ok && closeAfter && tabs.length) {
      await chrome.tabs.remove(tabs.map((t) => t.id));
    }
  } finally {
    btn.disabled = false;
  }
}

/* ---------- 看板地址与访问令牌 ---------- */

async function saveUrl() {
  const raw = $("boardUrl").value.trim().replace(/\/+$/, "");
  if (!isHttpUrl(raw)) {
    setStatus("地址须以 http:// 或 https:// 开头", "err");
    return;
  }
  const given = $("token").value.trim();
  await chrome.storage.local.set({ boardUrl: raw, token: given });
  boardUrl = raw;
  token = given;
  setStatus(given ? "地址与令牌已保存" : "地址已保存（令牌留空 = 请求不带 X-Token）", "ok");
}

/* ---------- 初始化 ---------- */

async function init() {
  await loadSettings();

  const tabs = await collectibleTabs();
  $("tabCount").textContent = String(tabs.length);
  $("saveAll").disabled = tabs.length === 0;

  /* 受限页面（chrome://、file://、商店页…）与本地回环页：禁用按钮并说明原因。
     反过来，把 popup.html 自己当标签页打开做自动化测试时也会走到这里——行为一致 */
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  if (!tab || !isHttpUrl(tab.url)) {
    $("saveCurrent").disabled = true;
    $("currentHint").hidden = false;
    $("currentHint").textContent = "当前标签不是网页（chrome:// / 文件等），无法读取元数据";
  } else if (isLoopback(tab.url)) {
    $("saveCurrent").disabled = true;
    $("currentHint").hidden = false;
    $("currentHint").textContent = "本地回环页（127.0.0.1 / localhost）不收藏";
  }

  $("saveCurrent").addEventListener("click", saveCurrent);
  $("saveAll").addEventListener("click", saveAll);
  $("saveUrl").addEventListener("click", saveUrl);
  $("closeAfter").addEventListener("change", async (e) => {
    closeAfter = e.target.checked;
    await chrome.storage.local.set({ closeAfter });
  });
  $("boardUrl").addEventListener("keydown", (e) => {
    if (e.key === "Enter") saveUrl();
  });
}

init();
