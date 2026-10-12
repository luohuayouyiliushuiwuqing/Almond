/* 左侧栏构建与「更多」溢出测量/展开。 */
import { emit } from "./bus.js";
import { goToDomain, leaveDomainPage } from "./domain-page.js";
import { $, MIN_GROUP, el, hostOf, rootDomain, state } from "./state.js";

/* 所有筛选动作共用：正在域名子页里点筛选 = 先退回看板再套筛选。
   不退的话子页会无视 selectedDomain，看着像点了没反应 */
function filterTo(apply) {
  leaveDomainPage();
  apply();
  emit("render");
}

/* ---------- 侧边栏 ---------- */

export function buildSidebar() {
  const sb = $("sidebar");
  const keepScroll = sb.scrollTop;   // 展开态重绘（如代理标记到达）不丢滚动位置
  sb.innerHTML = "";
  sb.classList.remove("expanded");   // 先按未展开测量，避免滚动条宽度干扰

  const allActive = !state.domainPage && state.selectedDomain === null && state.selectedTag === null;
  const all = el("div", "sidebar-item" + (allActive ? " active" : ""));
  all.innerHTML = `<span class="sidebar-name">全部</span><span class="sidebar-count">${state.records.length}</span>`;
  all.addEventListener("click", () => filterTo(() => {
    state.selectedDomain = null;
    state.selectedTag = null;
  }));
  sb.appendChild(all);

  /* ── 域名（少于 MIN_GROUP 条的零散域名收进「其他」） ── */
  sb.appendChild(el("div", "sidebar-label", "域名"));
  const domainCounts = new Map();
  for (const r of state.records) {
    const d = rootDomain(hostOf(r.url)) || "unknown";
    domainCounts.set(d, (domainCounts.get(d) || 0) + 1);
  }
  const sortedDomains = [...domainCounts.entries()].sort((a, b) => b[1] - a[1]);

  /* 域名行整行点击 = 直接进入该域名的子页（再点一次退回看板）。
     高亮跟着 state.domainPage 走（进入子页才亮），不再有 [编辑]/[详情] 两个入口——
     编辑进子页的「域名设置」，别名/代理/域名重置也都在那里；代理面板的域名列表仍是备用入口 */
  const domainItem = (label, count) => {
    const active = state.domainPage === label;
    const proxied = state.proxyDomains.has(label);
    const item = el("div", "sidebar-item" + (active ? " active" : "") + (proxied ? " proxy" : ""));
    let inner = `<span class="sidebar-name">${label}</span>`;
    if (proxied) inner += `<span class="proxy-badge" title="匹配代理规则：走代理">代理</span>`;
    inner += `<span class="sidebar-count">${count}</span>`;
    item.innerHTML = inner;
    item.title = active ? `再点一次返回看板` : `进入 ${label} 的子页面`;
    item.addEventListener("click", () => {
      if (state.domainPage === label) {
        leaveDomainPage();   // 内部自己 render（清状态 + 清 hash + 重绘看板）
      } else {
        goToDomain(label);   // hashchange → syncDomainRoute() → render()
      }
    });
    sb.appendChild(item);
    return item;
  };

  let minorTotal = 0;
  let minorProxied = 0;
  const minorNames = [];
  for (const [domain, count] of sortedDomains) {
    if (count < MIN_GROUP) {
      minorNames.push(domain);
      minorTotal += count;
      if (state.proxyDomains.has(domain)) minorProxied++;
      continue;
    }
    domainItem(domain, count);
  }
  if (minorNames.length) {
    const active = state.selectedDomain === "__other__";
    // 「其他」本身不在 proxyDomains 里：有任一成员走代理就打标
    const proxied = minorProxied > 0;
    const item = el("div", "sidebar-item" + (active ? " active" : "") + (proxied ? " proxy" : ""));
    let inner = `<span class="sidebar-name">其他</span>`;
    if (proxied) {
      inner += `<span class="proxy-badge" title="包含 ${minorProxied} 个走代理的域名">代理</span>`;
    }
    inner += `<span class="sidebar-count">${minorTotal}</span>`;
    item.innerHTML = inner;
    item.addEventListener("click", () => filterTo(() => {
      state.selectedDomain = active ? null : "__other__";
    }));
    sb.appendChild(item);
  }

  /* ── 标签 ── */
  sb.appendChild(el("div", "sidebar-label", "标签"));
  const tagCounts = new Map();
  let untagged = 0;
  for (const r of state.records) {
    if (!r.tags || !r.tags.length) { untagged++; continue; }
    for (const t of r.tags) tagCounts.set(t.name, (tagCounts.get(t.name) || 0) + 1);
  }
  const sortedTags = [...tagCounts.entries()].sort((a, b) => b[1] - a[1]);
  for (const [name, count] of sortedTags) {
    const active = state.selectedTag === name;
    const item = el("div", "sidebar-item" + (active ? " active" : ""));
    item.innerHTML = `<span class="sidebar-name">${name}</span><span class="sidebar-count">${count}</span>`;
    item.addEventListener("click", () => filterTo(() => {
      state.selectedTag = active ? null : name;
    }));
    sb.appendChild(item);
  }
  if (untagged) {
    const active = state.selectedTag === "__untagged__";
    const item = el("div", "sidebar-item" + (active ? " active" : ""));
    item.innerHTML = `<span class="sidebar-name">未标签</span><span class="sidebar-count">${untagged}</span>`;
    item.addEventListener("click", () => filterTo(() => {
      state.selectedTag = active ? null : "__untagged__";
    }));
    sb.appendChild(item);
  }

  applySidebarOverflow(sb);
  if (sb.classList.contains("expanded")) sb.scrollTop = keepScroll;
}

/* 侧栏固定高度、不随滚轮滚动：装不下的条目收进底部「更多」，点开才整列展开。
   展开态改用侧栏自身滚动条（overflow-y: auto），滚轮此时作用于侧栏。
   高度用 offsetTop 实测——它相对定位后的侧栏 padding 盒，已含各元素外边距。 */

let sidebarOverflowTop = 0;   // 首个被收起条目的位置，展开后滚到这里

function applySidebarOverflow(sb) {
  const kids = [...sb.children];
  if (!kids.length) return;

  const cs = getComputedStyle(sb);
  const padBottom = parseFloat(cs.paddingBottom) || 0;
  const probe = sb.querySelector(".sidebar-item");
  const rowH = probe ? probe.offsetHeight : 33;
  // 内容区底边（相对侧栏 border 盒），再减一行留给「更多」
  const limit = Math.max(sb.clientHeight - padBottom - rowH, 0);

  let split = kids.length;
  for (let i = 0; i < kids.length; i++) {
    if (kids[i].offsetTop + kids[i].offsetHeight > limit) { split = i; break; }
  }
  if (split >= kids.length) return;            // 装得下，无需「更多」
  sidebarOverflowTop = kids[split].offsetTop;

  const more = el("div", "sidebar-more");
  if (state.sidebarExpanded) {
    sb.classList.add("expanded");
    more.innerHTML = `<span>收起</span><span class="arrow">▴</span>`;
    more.addEventListener("click", () => { state.sidebarExpanded = false; buildSidebar(); });
  } else {
    for (let i = split; i < kids.length; i++) kids[i].hidden = true;
    more.innerHTML = `<span>更多 (${kids.length - split})</span><span class="arrow">▾</span>`;
    more.addEventListener("click", () => {
      state.sidebarExpanded = true;
      buildSidebar();
      sb.scrollTop = sidebarOverflowTop;   // 直接落在刚展开的位置
    });
  }
  sb.appendChild(more);
}
