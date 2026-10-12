/* Raindrop 风格 Web 看板 — 交互逻辑 */

"use strict";

import { recomputePendingGrids, render, syncCardIntrinsic } from "./board.js";
import { on } from "./bus.js";
import { backfillState, forceBackfill, handleBackfillClick, startBackfill, toggleBackfill } from "./backfill.js";
import { updateCard } from "./cards.js";
import { closeDetail } from "./detail.js";
import { applyPrefsTag, leaveDomainPage, syncDomainRoute } from "./domain-page.js";
import { addUrl, fetchRecord } from "./fetch.js";
import { doExport, importFromFile } from "./io.js";
import { addNewTag, submitRename } from "./modals.js";
import { addDomainRule, addRule, detectProxy, openProxyPanel, refreshDomainTests, saveDomain, saveGlobalProxy, startProxyPoll } from "./proxy-panel.js";
import { getVisibleRecords, loadDomainConfig, loadProxyDomains, loadRecords } from "./records.js";
import { initSelectBar } from "./selectbar.js";
import { buildSidebar } from "./sidebar.js";
import { $, setStatus, state } from "./state.js";
import { fsState, loadFs, openDbPanel, saveDbPath } from "./storage.js";


/* ---------- 事件绑定 ---------- */

function init() {
  /* render / updateCard 两条跨模块回调边走极简总线——订阅只在这一处（bus.js 契约） */
  on("render", render);
  on("updateCard", updateCard);

  $("sidebarToggle").addEventListener("click", () => {
    $("sidebar").classList.toggle("collapsed");
    // 宽度过渡结束后列数才定下来，届时同步卡片实测尺寸 + 重算占位高度
    clearTimeout(window.__sbReflowTimer);
    window.__sbReflowTimer = setTimeout(() => {
      syncCardIntrinsic();
      recomputePendingGrids();
    }, 240);
  });

  /* 滚轮落在侧边栏上时转发给右侧看板滚动——侧边栏自身固定不滑动。
     用 document 捕获阶段监听：先于一切默认滚动行为执行，防止被子元素干扰。
     「更多」展开后侧栏自己能滚（内容已超出一屏），此时不拦截，交还原生滚动。 */
  document.addEventListener(
    "wheel",
    (e) => {
      const sb = $("sidebar");
      if (!sb || sb.classList.contains("collapsed") || !sb.contains(e.target)) return;
      if (state.sidebarExpanded) return;   // 展开态：滚轮交给侧栏
      e.preventDefault();
      const wrap = document.querySelector(".board-wrap");
      if (wrap) wrap.scrollTop += e.deltaY;
    },
    { capture: true, passive: false }
  );

  /* 窗口尺寸变了 → 重测侧栏可见条目（否则缩小后底部条目被裁掉且没有「更多」），
     同时按新列数重算未建分组的占位高度 */
  let resizeTimer = 0;
  window.addEventListener("resize", () => {
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(() => {
      buildSidebar();
      syncCardIntrinsic();
      recomputePendingGrids();
    }, 150);
  });

  $("addBtn").addEventListener("click", addUrl);
  $("urlInput").addEventListener("keydown", (e) => {
    if (e.key === "Enter") addUrl();
  });
  $("importBtn").addEventListener("click", () => $("fileInput").click());
  $("fileInput").addEventListener("change", (e) => {
    const file = e.target.files[0];
    if (file) importFromFile(file);
    e.target.value = "";  // 允许重复选同一文件
  });
  /* 一个按钮两种用法：空闲点 = 强制补抓（含失败重试），跑着点 = 暂停/继续 */
  $("backfillBtn").addEventListener("click", handleBackfillClick);
  $("exportSelect").addEventListener("change", (e) => {
    const format = e.target.value;
    e.target.value = "";  // 复位，允许重复导出同格式
    if (format) doExport(format);
  });
  $("searchInput").addEventListener("input", (e) => {
    state.filter = e.target.value.trim().toLowerCase();
    render();
  });

  /* 多选操作条：按钮接线 + #board 上的复选框 change 委托（render 不会冲掉它） */
  initSelectBar();

  $("modalOk").addEventListener("click", submitRename);
  $("modalCancel").addEventListener("click", () => {
    $("modalMask").hidden = true;
    state.renameTarget = null;
  });
  $("modalInput").addEventListener("keydown", (e) => {
    if (e.key === "Enter") submitRename();
    if (e.key === "Escape") $("modalCancel").click();
  });
  $("modalMask").addEventListener("click", (e) => {
    if (e.target === $("modalMask")) $("modalCancel").click();
  });

  $("tagAddBtn").addEventListener("click", addNewTag);
  $("tagInput").addEventListener("keydown", (e) => {
    if (e.key === "Enter") addNewTag();
  });
  $("tagClose").addEventListener("click", () => {
    $("tagMask").hidden = true;
    state.tagTarget = null;
    state.tagTargets = null;   // 批量模式一起清，否则下次单条弹窗会拿到上一批目标
  });
  $("tagMask").addEventListener("click", (e) => {
    if (e.target === $("tagMask")) $("tagClose").click();
  });

  $("domainSave").addEventListener("click", saveDomain);
  $("domainCancel").addEventListener("click", () => {
    $("domainMask").hidden = true;
    state.domainTarget = null;
  });
  $("domainMask").addEventListener("click", (e) => {
    if (e.target === $("domainMask")) $("domainCancel").click();
  });

  $("proxyPanelBtn").addEventListener("click", openProxyPanel);
  $("proxyClose").addEventListener("click", () => { $("proxyMask").hidden = true; });
  $("proxyMask").addEventListener("click", (e) => {
    if (e.target === $("proxyMask")) $("proxyMask").hidden = true;
  });
  $("globalProxySave").addEventListener("click", saveGlobalProxy);
  $("proxyDetectBtn").addEventListener("click", () => detectProxy({ fill: true }));
  $("ruleAddBtn").addEventListener("click", addRule);
  $("rulePatternInput").addEventListener("keydown", (e) => { if (e.key === "Enter") addRule(); });
  $("ruleDomainAdd").addEventListener("click", addDomainRule);
  $("testRefreshBtn").addEventListener("click", refreshDomainTests);

  $("dbPanelBtn").addEventListener("click", openDbPanel);
  $("dbSave").addEventListener("click", saveDbPath);
  $("dbPathInput").addEventListener("keydown", (e) => {
    if (e.key === "Enter") saveDbPath();
  });
  $("dbBrowse").addEventListener("click", () => loadFs(fsState.path || fsState.storage || ""));
  $("fsUp").addEventListener("click", () => {
    if (fsState.parent) loadFs(fsState.parent);
  });
  $("fsPick").addEventListener("click", () => {
    if (fsState.path) {
      $("dbPathInput").value = fsState.path;
      $("fsPicker").hidden = true;
    }
  });
  $("dbClose").addEventListener("click", () => { $("dbMask").hidden = true; });
  $("dbMask").addEventListener("click", (e) => {
    if (e.target === $("dbMask")) $("dbClose").click();
  });

  $("detailClose").addEventListener("click", closeDetail);
  $("detailRefresh").addEventListener("click", async () => {
    const target = state.detailTarget;
    if (!target) return;
    $("detailRefresh").disabled = true;
    try {
      await fetchRecord(target.url);
    } finally {
      $("detailRefresh").disabled = false;
    }
  });
  $("detailMask").addEventListener("click", (e) => {
    if (e.target === $("detailMask")) closeDetail();
  });
  document.addEventListener("keydown", (e) => {
    if (e.key !== "Escape") return;
    if (!$("detailMask").hidden) { closeDetail(); return; }
    /* 子页里 Esc = 返回看板；有其它弹窗开着时优先关弹窗，不退页 */
    if (state.domainPage && !anyModalOpen()) leaveDomainPage();
  });

  loadProxyDomains(); // 先拿到走代理域名集合，回来后自动 render 打标
  /* 路由先定视图：放在加载链之前，带 #/domain/xxx 打开时首屏直接是子页，不闪看板 */
  window.addEventListener("hashchange", () => { if (syncDomainRoute()) render(); });
  syncDomainRoute();
  /* 自动补抓排在「记录、代理、域名配置」都就位之后：
     不等 loadRecords 则 state.records 还是空的，一条都挑不出来；
     不等 detectProxy 则前几条会拿默认代理地址去抓外网；
     不等 loadDomainConfig 则 auto_fetch=0 的域名过滤不到（轮询还会每 10 秒空转一次）。
     链式不阻塞界面，init 仍保持同步函数（顶层 async function 会撞循环规则检查）。 */
  loadRecords()
    .then(() => detectProxy({ fill: true, announce: false }))
    .then(() => loadDomainConfig())
    .then(() => {
      const before = state.selectedTag;
      applyPrefsTag();                  // 子页：套用该域名存的标签偏好
      /* 配置到位后子页必须重绘一次：第一帧渲染时 domainConfig 还是空的，
         排序/展示字段/别名/抓取规则全是默认值，不重绘就一直停在默认上 */
      if (state.domainPage || state.selectedTag !== before) render();
      startBackfill();
    });
  /* 被动跟随服务端的代理定时刷新（每 30s 读一次 /api/proxy-current 缓存）：
     不参与上面的加载链——它只消费结论，早一轮晚一轮无所谓 */
  startProxyPoll();
}

/* 有没有弹窗开着（detailMask 单独处理，Esc 的第一优先级） */
function anyModalOpen() {
  for (const id of ["modalMask", "tagMask", "domainMask", "proxyMask", "dbMask"]) {
    const m = $(id);
    if (m && !m.hidden) return true;
  }
  return false;
}

document.addEventListener("DOMContentLoaded", init);

/* 控制台调试句柄：模块内函数不再挂 window，排查时从这里取 */
window.XR = {
  backfill: {
    start: startBackfill, force: forceBackfill, click: handleBackfillClick,
    toggle: toggleBackfill, state: backfillState,
  },
  domain: { leave: leaveDomainPage, sync: syncDomainRoute },
  buildSidebar, fetchRecord, getVisibleRecords, loadRecords, render, setStatus, state, updateCard,
};
