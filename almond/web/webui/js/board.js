/* render() 及其专属机制：分片建卡、懒建观察者、占位高度、滚动保持。 */
import { createCard } from "./cards.js";
import { renderDomainPage } from "./domain-page.js";
import { getVisibleRecords } from "./records.js";
import { syncSelectBar } from "./selectbar.js";
import { buildSidebar } from "./sidebar.js";
import { $, MIN_GROUP, el, hostOf, rootDomain, setStatus, state } from "./state.js";

/* ---------- 渲染 ---------- */

/* 分组分片建卡片：每帧 100 张，既不卡顿也不会一次建出全部节点。
   onDone 在**全部建完**时调用——补建期间占位高度一直留着，
   高度不随分片增长而变，滚动位置才不会被顶走。
   导出给 domain-page 的卡片视图复用（域名子页不分组、不占位，onDone 不传） */
export function appendCardsChunked(grid, recs, start = 0, onDone) {
  const CHUNK = 100;
  const end = Math.min(start + CHUNK, recs.length);
  for (let i = start; i < end; i++) grid.appendChild(createCard(recs[i]));
  if (end < recs.length) {
    requestAnimationFrame(() => {
      if (grid.isConnected) appendCardsChunked(grid, recs, end, onDone);
    });
  } else if (onDone) {
    onDone();
  }
}

/* ---------- 分组卡片按需建：只建视口附近的分组 ---------- */
const CARD_MIN = 240;      // style.css 的 --card-w

const CARD_GAP = 16;       // .board-grid / .domain-grid 的 gap

const CARD_BODY_H = 86.37; // 卡片正文（标题 2 行 + 域名行）实测高度

let groupObserver = null;

/* 所有分组网格同宽，所以**只读一次 clientWidth**：读取会强制重排，
   在循环里逐个「读宽 + 写 minHeight」等于把整块看板重排几十遍 */

function gridMetrics() {
  const g = document.querySelector("#board .domain-grid, #board .board-grid");
  const w = g ? g.clientWidth : 0;
  if (!w) return null;
  const cols = Math.max(1, Math.floor((w + CARD_GAP) / (CARD_MIN + CARD_GAP)));
  const colW = (w - (cols - 1) * CARD_GAP) / cols;
  return { cols, rowH: (colW - 2) * 9 / 16 + CARD_BODY_H };
}

/* 未建卡片的分组先按公式占位高度：缩略图 16:9 + 固定正文。
   三个视口实测与真实值一致（1280→220.81 / 1440→243.31 / 1920→232.63），
   占准了滚动条才不会在补建时跳动 */

function reserveHeight(grid, recs, m) {
  if (!recs.length || !m) return;
  const rows = Math.ceil(recs.length / m.cols);
  grid.dataset.pending = "1";
  grid.style.minHeight = Math.round(rows * m.rowH + (rows - 1) * CARD_GAP) + "px";
}

/* 被 content-visibility 跳过的卡片按 contain-intrinsic-size 占位，而这个值随列宽变
   （1280→218.81 / 1440→241.31 / 1920→230.63，都是**内容盒**）。写死会与真实渲染尺寸
   不一致 → 每行差 1~22px、几十行累计上千像素，占位高度和自然高度对不上。
   这里取一张**视口内**卡片的实测值覆盖；跳过的卡片报告的是估算值，不能当基准 */

let cardIntrinsicKey = "";

export function syncCardIntrinsic() {
  let hit = null;
  for (const c of document.querySelectorAll("#board .card")) {
    const r = c.getBoundingClientRect();
    if (r.bottom > 0 && r.top < innerHeight && r.width && r.height) { hit = c; break; }
  }
  if (!hit) return;
  const key = `${hit.clientWidth}x${hit.clientHeight}`;
  if (key === cardIntrinsicKey) return;
  cardIntrinsicKey = key;
  let tag = document.getElementById("cardIntrinsic");
  if (!tag) {
    tag = document.createElement("style");
    tag.id = "cardIntrinsic";
    document.head.appendChild(tag);
  }
  tag.textContent =
    `.card{contain-intrinsic-size:auto ${hit.clientWidth}px auto ${hit.clientHeight}px}`;
}

function fillGrid(grid) {
  if (!grid || grid.dataset.pending !== "1" || grid.dataset.filling === "1") return;
  grid.dataset.filling = "1";
  appendCardsChunked(grid, grid.__recs || [], 0, () => {
    delete grid.dataset.filling;
    delete grid.dataset.pending;
    grid.style.minHeight = "";   // 建完才撤占位：此时自然高度 == 占位高度
  });
  syncCardIntrinsic();           // 首片卡片已同步建出，可以量真实尺寸
}

/* 侧栏宽度 / 窗口尺寸变了 → 列数与行高随之变化，占位要跟着重算（量一次、写一批） */

export function recomputePendingGrids() {
  const grids = document.querySelectorAll('[data-pending="1"]');
  if (!grids.length) return;
  const m = gridMetrics();
  for (const g of grids) reserveHeight(g, g.__recs || [], m);
}

function resetGroupObserver() {
  if (groupObserver) groupObserver.disconnect();
  groupObserver = new IntersectionObserver(
    (entries) => {
      for (const e of entries) {
        if (!e.isIntersecting || e.target.classList.contains("collapsed")) continue;
        groupObserver.unobserve(e.target);   // 折叠中的先留着，等分组头点开再建
        fillGrid(e.target.__grid);
      }
    },
    { root: document.querySelector(".board-wrap"), rootMargin: "800px 0px" }
  );
}

/* 同一视图内重渲（重新抓取完成、改名、删除、代理标记到达……）必须**保住滚动位置**：
   board.innerHTML="" 会把 .board-wrap 的 scrollTop 压到 0，重建后就停在最上面。
   筛选条件变了（搜索词 / 域名 / 标签）则另当别论——换视图从头看，回顶部 */

let lastViewKey = null;

export function render() {
  const board = $("board");
  const wrap = document.querySelector(".board-wrap");
  const prevScroll = wrap ? wrap.scrollTop : 0;
  /* viewKey 必须带上 domainPage：看板 ↔ 子页是两个视图，
     不带的话切过去会被当成「同一视图」，滚动位置会乱串 */
  const viewKey = [state.filter, state.selectedDomain, state.selectedTag, state.domainPage || ""].join(" ");
  const sameView = viewKey === lastViewKey;
  lastViewKey = viewKey;

  board.innerHTML = "";
  buildSidebar();

  const visible = getVisibleRecords();

  /* 离开子页要收起域头：它在 #board 外面，render() 的清空动作管不到它，
     不在这里显式收起的话，退回看板后会留着一条过期的域名头 */
  const dh = $("domainHeader");
  if (dh) dh.hidden = true;

  /* ── 域名子页：口径已在 getVisibleRecords() 里按 domainPage 过滤好 ── */
  if (state.domainPage) {
    resetGroupObserver();                 // 拆掉上一屏分组留下的观察者
    renderDomainPage(board, visible);
    /* emptyHint 保持隐藏：列表自带「没有符合条件的记录」空态，两个提示会重叠 */
    $("emptyHint").hidden = true;
    const all = state.records.filter((r) => r.domain === state.domainPage).length;
    setStatus(`域名 ${state.domainPage}，共 ${all} 条，${visible.length} 条显示`);
    if (wrap) wrap.scrollTop = sameView ? prevScroll : 0;
    syncSelectBar();                      // 选择计数与子页行保持一致
    return;
  }

  /* 按域名分组显示 */
  let domainMap = new Map();
  for (const r of visible) {
    const d = rootDomain(hostOf(r.url)) || "unknown";
    (domainMap.get(d) ?? domainMap.set(d, []).get(d)).push(r);
  }

  /* 少于 MIN_GROUP 条的零散域名收拢成一个「其他」类别；
     已筛选到单一域名时不收拢（否则该域名自己会被吞进「其他」）；
     「__other__」视图本身就是零散域名集合，照常收拢成一组 */
  if (!state.selectedDomain || state.selectedDomain === "__other__") {
    const major = new Map();
    const minor = [];
    for (const [d, recs] of domainMap) {
      if (recs.length < MIN_GROUP) minor.push(...recs);
      else major.set(d, recs);
    }
    if (minor.length) major.set("其他", minor);
    domainMap = major;
  }

  const sortedDomains = [...domainMap.entries()].sort((a, b) => b[1].length - a[1].length);

  /* 分组结构默认展开，但**卡片不一次性建完**：只给分组头 + 占位高度，
     滚到视口附近（rootMargin 800px）才补建该组卡片——
     855 条（或导入 5000 条）时首屏只建看得见的那几百个节点 */
  resetGroupObserver();
  if (sortedDomains.length <= 1 && visible.length <= 20) {
    // 平铺也包一层多列网格：.board 是纵向 flex，直接塞卡片会排成一列
    const grid = document.createElement("div");
    grid.className = "board-grid";
    for (const r of visible) grid.appendChild(createCard(r));
    board.appendChild(grid);
  } else {
    const groups = [];
    for (const [domain, recs] of sortedDomains) {
      const section = document.createElement("div");
      section.className = "domain-group";
      const header = document.createElement("div");
      header.className = "domain-header";
      header.innerHTML = `<span class="domain-toggle">▸</span><span class="domain-name">${domain}</span><span class="domain-count">${recs.length}</span>`;
      const grid = document.createElement("div");
      grid.className = "domain-grid";
      section.append(header, grid);
      section.__grid = grid;
      board.appendChild(section);

      header.addEventListener("click", () => {
        const opening = section.classList.contains("collapsed");
        if (opening) {                  // 手动展开：立即补建，不等滚动
          groupObserver.unobserve(section);
          fillGrid(grid);
        }
        section.classList.toggle("collapsed");
      });
      groups.push({ section, grid, recs });
    }

    /* 分组全部入 DOM 后**统一量一次**宽度，再批量写占位高度——
       占位先于建卡，卡片等滚到视口附近（rootMargin 800px）再补 */
    const m = gridMetrics();
    for (const { section, grid, recs } of groups) {
      grid.__recs = recs;
      reserveHeight(grid, recs, m);
      groupObserver.observe(section);
    }
  }

  $("emptyHint").hidden = visible.length > 0;
  const parts = [`共 ${state.records.length} 条`];
  if (state.selectedDomain) {
    parts.push(state.selectedDomain === "__other__" ? "其他" : state.selectedDomain);
  }
  if (state.selectedTag && state.selectedTag !== "__untagged__") parts.push(state.selectedTag);
  if (state.selectedTag === "__untagged__") parts.push("未标签");
  parts.push(`${visible.length} 条显示`);
  setStatus(parts.join("，"));

  /* 回到原滚动位置：此刻所有分组的占位高度都已写好，scrollHeight 是最终值 */
  if (wrap) wrap.scrollTop = sameView ? prevScroll : 0;
  syncCardIntrinsic();   // 平铺分支此刻已有卡片；分组分支由 fillGrid 触发
  /* 重建后刷新多选：剪掉已删除记录的 url、重算「已选 N 条」。
     勾选态本身由 createCard 读 state 恢复，这里不用碰卡片；必须放在
     setStatus 之后（syncSelectBar 不写状态栏，但顺序上放最后最稳） */
  syncSelectBar();
}
