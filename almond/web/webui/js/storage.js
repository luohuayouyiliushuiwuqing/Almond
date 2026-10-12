/* 存储目录面板与目录点选。
   切换语义（用户定的）：**不迁移，新目录从零开始**——旧数据留在原地；
   切换瞬间：中止在途抓取（服务端存储纪元兜底丢弃结果）、清空补抓队列与
   选择/搜索/筛选/子页等界面状态，然后全量加载新目录并重启补抓扫描。 */
import { abortAll, apiDel, apiGet, apiPost } from "./api.js";
import { emit } from "./bus.js";
import { resetBackfill, startBackfill } from "./backfill.js";
import { closeDetail } from "./detail.js";
import { leaveDomainPage } from "./domain-page.js";
import { loadRecords, reloadAll } from "./records.js";
import { $, el, setStatus, state } from "./state.js";

/* ---------- 存储目录（数据库、缓存等本地私有数据的统一存放处） ---------- */
export const fsState = { path: "", parent: "", storage: "" };

function fsJoin(dir, name) {
  if (!dir) return name;
  const sep = dir.includes("\\") ? "\\" : "/";
  return /[\\\/]$/.test(dir) ? dir + name : dir + sep + name;
}

/* ---------- 历史位置：点击直接切换（省去重新浏览选目录） ---------- */

function renderHistory(history) {
  const box = $("dbHistory");
  box.innerHTML = "";
  if (!history || !history.length) {
    box.appendChild(el("div", "fs-empty", "暂无历史记录"));
    return;
  }
  for (const p of history) {
    const isCurrent = p === fsState.storage;
    const row = el("div", "db-hist-row" + (isCurrent ? " current" : ""));
    const btn = el("button", "db-hist-item", p);
    btn.title = isCurrent ? `${p}（当前）` : `${p}（点击切换到此目录）`;
    if (isCurrent) {
      btn.disabled = true;
      row.appendChild(btn);
      row.appendChild(el("span", "db-hist-cur", "当前"));
    } else {
      btn.addEventListener("click", async () => {
        $("dbPathInput").value = p;   // 填入再走统一保存流程（切库、断旧任务、刷新记录都在里面）
        await saveDbPath();
      });
      row.appendChild(btn);
      const del = el("button", "db-hist-del", "移除");
      del.title = "从历史中移除这个路径";
      del.addEventListener("click", async () => {
        try {
          const d = await apiDel("/api/storage-history?path=" + encodeURIComponent(p));
          if (d.ok) renderHistory(d.history);
        } catch (e) {
          setStatus("移除历史失败：" + e.message, "err");
        }
      });
      row.appendChild(del);
    }
    box.appendChild(row);
  }
}

export async function openDbPanel() {
  $("dbMask").hidden = false;
  $("fsPicker").hidden = true;
  /* 路径输入框在**发起 GET 之前**就清空：放在 await 之后的话，GET 返回时会把
     用户这几毫秒里刚填/刚粘贴的路径冲掉（保存按钮于是报「请先选择或输入」） */
  $("dbPathInput").value = "";
  $("dbCurrent").textContent = "加载中…";
  try {
    const d = await apiGet("/api/storage-dir");
    fsState.storage = d.path;
    $("dbCurrent").textContent =
      `目录: ${d.path}\n数据库: ${d.db_path}\n图片缓存: ${d.cache_dir}`;
    $("dbCurrent").style.whiteSpace = "pre-line";
    if (d.exists === false) {
      $("dbCurrent").textContent +=
        "\n⚠ 目录不存在（可能已被移动或删除）——在下方重新选择一个目录，或输入同一路径点保存以重建";
      $("dbCurrent").classList.add("missing");
      setStatus("存储目录不存在：" + d.path, "err");
    } else {
      $("dbCurrent").classList.remove("missing");
    }
    renderHistory(d.history || []);
  } catch (e) {
    $("dbCurrent").textContent = "读取失败：" + e.message;
  }
}

export async function loadFs(path) {
  const box = $("fsList");
  $("fsPicker").hidden = false;
  $("fsPath").textContent = path || "（选择盘符 / 根目录）";
  box.innerHTML = '<div class="fs-empty">加载中…</div>';
  try {
    const d = await apiGet("/api/fs/list?path=" + encodeURIComponent(path));
    if (!d.ok) {
      box.innerHTML = "";
      box.appendChild(el("div", "fs-empty", d.error || "读取失败"));
      return;
    }
    fsState.path = d.path;
    fsState.parent = d.parent;
    $("fsPath").textContent = d.path || "（选择盘符 / 根目录）";
    $("fsUp").disabled = !d.parent;
    box.innerHTML = "";
    if (!d.entries.length) {
      box.appendChild(el("div", "fs-empty", "（无子目录）"));
      return;
    }
    for (const name of d.entries) {
      const btn = el("button", "fs-item", name);
      btn.addEventListener("click", () => loadFs(fsJoin(d.path, name)));
      box.appendChild(btn);
    }
  } catch (e) {
    box.innerHTML = "";
    box.appendChild(el("div", "fs-empty", "读取失败：" + e.message));
  }
}

/* 切换失败后的恢复：服务端仍是旧目录（POST 失败 = 什么都没切），
   把刚清掉的界面按旧目录加载回来、重启补抓轮。
   abort/reset 已经发生过：被中止的在途抓取服务端多半已写回旧库，loadRecords
   能拿回来；没写回的仍是 fetched=0，重新扫描会再入队——一轮不会丢任务。 */
async function recoverSwitch(reason) {
  try {
    await loadRecords();
    startBackfill("切换失败，已恢复原目录");
  } catch (e) { /* loadRecords 自己会报状态栏 */ }
  setStatus(`切换失败：${reason}（已恢复原目录）`, "err");
}

export async function saveDbPath() {
  const path = $("dbPathInput").value.trim();
  if (!path) {
    setStatus("请先选择或输入存储目录", "err");
    return;
  }
  $("dbSave").disabled = true;
  const t0 = Date.now();
  try {
    /* ① 先断后路，再发切换请求（**顺序与旧版相反**）：
       在途的 POST /api/fetch 占着浏览器同源连接池——HTTP/1.1 每源上限 6 条，
       5 个补抓 worker 加慢图/手动抓取能把槽位占满，单条还一跑几十秒。
       旧顺序「先 POST、成功后再中止」会让切换请求排队等空闲槽——实测能等到
       一分多钟。先中止：连接立刻归还、队列代际作废；服务端的纪元兜底不受影响，
       切换 POST 本身失败时走 recoverSwitch 恢复原样（服务端那时什么都没动）。 */
    setStatus("正在切换存储目录：中止旧任务…", "busy");
    abortAll();
    resetBackfill();

    /* ② 旧画面立刻清掉：点击即见变化；旧缩略图的 /api/img 随 DOM 一起被浏览器
       取消，连接池进一步腾出。失败恢复由 recoverSwitch 负责，所以可以先清 */
    state.records = [];
    state.selectedUrls.clear();
    state.filter = "";
    $("searchInput").value = "";
    state.selectedDomain = null;
    state.selectedTag = null;
    state.renameTarget = null;
    state.tagTarget = null;
    state.tagTargets = null;
    state.domainTarget = null;
    closeDetail();
    leaveDomainPage();          // 退出域名子页（在子页里时内部 render 一次）
    state.domainConfig = new Map();    // 旧域名配置绝不带进新目录
    state.proxyDomains = new Set();
    emit("render");             // 空看板立刻上屏（不在子页时 leaveDomainPage 不渲染）

    /* ③ 切库（服务端纪元 +1，旧纪元的抓取结果从此作废） */
    setStatus("正在切换存储目录：提交新目录…", "busy");
    let data;
    try {
      data = await apiPost("/api/storage-dir", { path });
    } catch (e) {
      await recoverSwitch(e.message);
      return;
    }
    if (!data.ok) {
      await recoverSwitch(data.error || "未知错误");
      return;
    }

    fsState.storage = data.path;
    if (typeof data.epoch === "number") state.storageEpoch = data.epoch;   // 新纪元立即生效
    $("dbCurrent").textContent =
      `目录: ${data.path}\n数据库: ${data.db_path}\n图片缓存: ${data.cache_dir}`;
    renderHistory(data.history || []);   // 新目录已进历史，列表跟着刷新
    $("dbPathInput").value = "";
    $("fsPicker").hidden = true;

    // ④ 并行加载新目录 + 重启补抓扫描/轮询（三个接口互不依赖，串行要白等三趟往返）
    setStatus("正在切换存储目录：载入新目录…", "busy");
    await reloadAll();
    startBackfill("存储已切换，扫描新目录");
    setStatus(
      `存储目录已切换到 ${data.path}` +
        (data.db_used_existing
          ? "（使用该目录现有数据库）"
          : "（新目录，从零开始）") +
        `；旧任务已中止、状态已重置 · 共 ${data.records} 条` +
        ` · 耗时 ${Date.now() - t0}ms`
    );
    // 卡在哪一步看状态栏的阶段文案（中止旧任务/提交新目录/载入新目录）即可分辨；
    // 服务端另有 set_storage_dir / count 分段日志（server.py）
  } finally {
    $("dbSave").disabled = false;
  }
}
