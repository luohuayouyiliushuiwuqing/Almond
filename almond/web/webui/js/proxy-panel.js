/* 代理映射面板 + 域名管理弹窗（它唯一的开启方）。 */
import { abortAll, apiDel, apiGet, apiPatch, apiPost } from "./api.js";
import { backfillState, startBackfill } from "./backfill.js";
import { buildSettingsForm, saveDomainSettings } from "./domain-settings.js";
import { goToDomain } from "./domain-page.js";
import { loadDomainConfig, loadProxyDomains } from "./records.js";
import { $, el, setStatus, state } from "./state.js";

/* ---------- 域名管理（表单与保存/重置逻辑在 domain-settings.js，此处只接弹窗收尾） ---------- */

let domainBox = null;   // 弹窗里挂的表单（saveDomain 从它收集字段）

function openDomain(domainName) {
  state.domainTarget = domainName;
  $("domainTitle").textContent = `域名管理：${domainName}`;
  $("domainMask").hidden = false;
  const host = $("domainFormHost");
  host.innerHTML = "";
  domainBox = null;
  // 从 API 获取域名信息后挂载表单（精简版：别名 + 代理 + 域名重置）
  apiGet("/api/domains").then(data => {
    if (state.domainTarget !== domainName) return;   // 期间已关/切走
    const cfg = (data.domains || []).find(d => d.name === domainName) || {};
    domainBox = buildSettingsForm(domainName, cfg, {
      fields: ["alias", "proxy", "reset"],
      compact: true,
      onResetDone: (data2) => {
        $("domainMask").hidden = true;
        state.domainTarget = null;
        if (state.selectedDomain === domainName) state.selectedDomain = null;
        /* 正看着这个域名的子页 → 把子页切到新域名（旧域名已经不存在了）。
           先写 hash：下面 await 期间 hashchange 会把 state.domainPage 更新好 */
        if (state.domainPage === domainName) goToDomain(data2.new);
      },
    });
    host.appendChild(domainBox);
  }).catch(() => setStatus("域名信息加载失败", "err"));
}

export async function saveDomain() {
  const name = state.domainTarget;
  if (!name || !domainBox) return;
  await saveDomainSettings(name, domainBox, {
    onDone: () => {
      $("domainMask").hidden = true;
      state.domainTarget = null;
    },
  });
}

/* ---------- 代理映射面板 ---------- */

export async function openProxyPanel() {
  $("globalProxyInput").value = state.globalProxy;
  /* 先开面板、后填内容：旧版 await renderProxyPanel()（两个 GET）之后才
     unmask——抓取占着连接池/服务端忙时，面板「打不开」、代理也改不了。
     现在打开是纯本地动作（同步显示），列表随后异步填充；保存按钮立刻可用。 */
  $("proxyMask").hidden = false;
  renderProxyPanel();
}

async function renderProxyPanel() {
  /* URL 模式规则 */
  const ruleBox = $("ruleList");
  ruleBox.innerHTML = "";
  try {
    const data = await apiGet("/api/proxy-rules");
    for (const rule of data.rules || []) {
      const row = el("div", "rule-row");
      row.appendChild(el("span", "rule-pattern", rule.pattern));
      row.appendChild(el("span", "rule-proxy" + (rule.need_proxy ? "" : " direct"),
        rule.need_proxy ? "用代理" : "直连"));
      const del = el("button", "rule-del", "删除");
      del.addEventListener("click", async () => {
        await apiDel(`/api/proxy-rule?id=${rule.id}`);
        renderProxyPanel();
        loadProxyDomains(); // 规则删除 → 刷新侧边栏代理标记
      });
      row.appendChild(del);
      ruleBox.appendChild(row);
    }
    if (!(data.rules || []).length) ruleBox.appendChild(el("div", "rule-empty", "暂无规则"));
  } catch { ruleBox.textContent = "加载失败"; }

  /* 弹窗两扇区（结构在 index.html 里）：
     左 .proxy-side = 系统实测（fetch_hints，只读徽标；整行点击进子页——
       零散域名 <MIN_GROUP 没有侧栏行，这里是它唯一的子页入口，域名设置在子页里）
     右 .proxy-main = 用户指定的全部关系：全局代理 / URL 模式规则 / 域名路由 */
  const dRuleBox = $("domainRuleList");
  const dTestBox = $("domainTestList");
  const selBox = $("ruleDomainSel");
  dRuleBox.innerHTML = "";
  dTestBox.innerHTML = "";
  selBox.innerHTML = '<option value="">选择域名…</option>';
  try {
    const data = await apiGet("/api/domains");
    const all = (data.domains || []).slice().sort((a, b) => a.name.localeCompare(b.name));
    const isSpecified = (d) => d.need_proxy !== null && d.need_proxy !== undefined;

    /* ── 右：域名路由（用户指定，可改） ── */
    const specified = all.filter(isSpecified);
    for (const d of specified) {
      const row = el("div", "rule-row");
      const nameBtn = el("span", "rule-pattern link", d.name);
      nameBtn.title = "点域名名：编辑别名 / 域名重置";
      nameBtn.addEventListener("click", () => { $("proxyMask").hidden = true; openDomain(d.name); });
      row.appendChild(nameBtn);

      const need = el("select", "input rule-need");
      need.innerHTML = `<option value="1">用代理</option><option value="0">直连</option>`;
      need.value = d.need_proxy ? "1" : "0";
      need.title = "用户指定的路由，可随时改";
      need.addEventListener("change", () => patchNeedProxy(d.name, need.value === "1"));
      row.appendChild(need);

      const unset = el("button", "rule-del", "取消");
      unset.title = "取消指定，回到跟随全局";
      unset.addEventListener("click", () => patchNeedProxy(d.name, null));
      row.appendChild(unset);
      dRuleBox.appendChild(row);
    }
    if (!specified.length) {
      dRuleBox.appendChild(el("div", "rule-empty", "还没有指定任何域名——从下方选择添加"));
    }

    /* 底部「选择域名」下拉：只列尚未指定的 */
    for (const d of all.filter((d) => !isSpecified(d))) {
      const opt = el("option", null, d.name);
      opt.value = d.name;
      selBox.appendChild(opt);
    }

    /* ── 左：系统实测（只读徽标，整行点击进子页） ── */
    const verdict = (t) => {
      if (!t) return { text: "未测试", cls: "none", title: "系统还没抓取过这个域名；自动抓取前会先自动确认方案" };
      // 方案确认阶段的结论优先（自动抓取的放行/跳过依据）
      if (t.confirm && t.confirm.ok === false) {
        return { text: "无方案", cls: "bad",
                 title: `系统实测：${t.confirm.reason || "直连/代理/JS 多路尝试均未取得数据"}（${t.confirm.at || "时间未知"}）——自动抓取整组跳过` };
      }
      if (t.confirm && t.plan !== true) {
        return { text: "方案失效", cls: "bad",
                 title: `系统实测：确认过（${t.confirm.at || "时间未知"}）但当前无可用方案——自动抓取跳过，人工强制补抓仍可试` };
      }
      if (t.direct === true) {
        return { text: "直连", cls: "ok", title: `系统实测：直连可达（${t.updated || "时间未知"}）` };
      }
      if (t.direct === false && t.proxy === true) {
        return { text: "代理", cls: "proxy", title: `系统实测：直连不通、经代理可达（${t.updated || "时间未知"}）` };
      }
      if (t.direct === false) {
        return { text: "须代理", cls: "warn", title: `系统实测：直连不通，代理尚未验证可用（${t.updated || "时间未知"}）` };
      }
      if (t.proxy === true) {
        return { text: "代理", cls: "proxy", title: `系统实测：经代理可达（${t.updated || "时间未知"}）` };
      }
      return { text: "未测试", cls: "none", title: "没有直连实测结论" };
    };
    const testedCount = all.filter((d) => d.tested).length;
    $("testColCount").textContent = `${testedCount}/${all.length}`;
    // 有实测的排前面，其次按名字
    const rightOrder = all.slice().sort(
      (a, b) => (b.tested ? 1 : 0) - (a.tested ? 1 : 0) || a.name.localeCompare(b.name)
    );
    for (const d of rightOrder) {
      const row = el("div", "rule-row test-row");
      const v = verdict(d.tested);
      // 有活跃方案的把方案 id 带进悬浮说明（系统实测的抓取方案）
      if (d.tested && d.tested.strategy) v.title += `；方案 ${d.tested.strategy}`;
      const nameBtn = el("span", "rule-pattern link", d.name);
      nameBtn.title = `${v.title}；点击进入 ${d.name} 子页`;
      nameBtn.addEventListener("click", () => {
        $("proxyMask").hidden = true;
        goToDomain(d.name);
      });
      row.appendChild(nameBtn);

      const badge = el("span", `test-badge ${v.cls}`, v.text);
      badge.title = v.title;
      row.appendChild(badge);
      dTestBox.appendChild(row);
    }
    if (!all.length) dTestBox.appendChild(el("div", "rule-empty", "还没有任何域名"));
  } catch {
    dRuleBox.textContent = "加载失败";
    dTestBox.textContent = "加载失败";
  }
}

/* 改一个域名的用户规则（need_proxy：true/false/null=取消指定跟随全局）。
   只动左栏数据；右栏实测结论来自 fetch_hints，与本接口无关 */

/* ---------- 刷新未测试/失败的域名方案确认 ---------- */

export async function refreshDomainTests() {
  const btn = $("testRefreshBtn");
  if (!btn) return;
  // 收集需要刷新的域名：未测试 / 无方案 / 方案失效
  const data = await apiGet("/api/domains");
  const todo = (data.domains || [])
    .filter((d) => {
      const t = d.tested;
      if (!t) return true;                                    // 未测试
      if (t.confirm && t.confirm.ok === false) return true;   // 无方案
      if (t.confirm && t.plan !== true) return true;          // 方案失效
      return false;
    })
    .map((d) => d.name);
  if (!todo.length) { setStatus("所有域名均已确认且有可用方案，无需刷新"); return; }

  btn.disabled = true;
  btn.textContent = "确认中…";

  /* ── 暂停补抓，让出子进程池给确认阶段 ──
     确认和补抓共用同一个 8 进程子进程池（fetchpool；前端补抓 5 并发），同时跑会互相挤占：
     补抓占满 5 个 → 确认排队等；确认占满 → 补抓排队等。
     策略：暂停补抓 + 中止在途连接 → 子进程池全给确认 → 跑完再恢复补抓。 */
  const wasPaused = backfillState.paused;
  backfillState.paused = true;   // 仅内存态：不写 localStorage，恢复时按原值还原
  abortAll();              // 中止在途的 /api/fetch 轮询连接，释放浏览器连接槽

  setStatus(`正在重新确认 ${todo.length} 个域名的抓取方案…`, "busy");
  try {
    const result = await apiPost("/api/confirm", { domains: todo, proxy: state.globalProxy });
    if (result.stale) { setStatus("存储已切换，刷新作废"); return; }
    // 刷新面板
    await loadDomainConfig();
    renderProxyPanel();
    const parts = [];
    if ((result.confirmed || []).length) parts.push(`新确认 ${result.confirmed.length} 个`);
    if ((result.ready || []).length) parts.push(`已有方案 ${result.ready.length} 个`);
    if ((result.failed || []).length) parts.push(`无方案 ${result.failed.length} 个`);
    if ((result.transient || []).length) parts.push(`临时失败 ${result.transient.length} 个`);
    setStatus(parts.length ? `刷新完成：${parts.join("，")}` : "刷新完成");
  } catch (e) {
    setStatus("刷新失败：" + e.message, "err");
  } finally {
    /* ── 恢复补抓 ── */
    backfillState.paused = wasPaused;
    btn.disabled = false;
    btn.textContent = "刷新";
    // 重启补抓：若之前没暂停，重新扫一遍待抓记录并启动轮询；
    // 若之前已暂停，startBackfill 读 localStorage 的暂停位会正确保持暂停
    startBackfill();
  }
}

async function patchNeedProxy(name, need_proxy) {
  try {
    const data = await apiPatch("/api/domain", { name, need_proxy });
    if (!data.ok) { setStatus("更新失败：" + data.error, "err"); return; }
    const label = need_proxy === null ? "跟随全局（已取消指定）" : (need_proxy ? "用代理" : "直连");
    setStatus(`已指定 ${name} → ${label}`);
    loadProxyDomains();   // 生效路由可能变 → 刷新侧栏代理徽标
    renderProxyPanel();   // 重建两栏（条数、下拉候选）
  } catch (e) {
    setStatus("域名更新失败：" + e.message, "err");
  }
}

/* 底部「指定」按钮：给尚未指定的域名加一条用户规则 */
export async function addDomainRule() {
  const name = $("ruleDomainSel").value;
  if (!name) { setStatus("请先选择要指定的域名", "err"); return; }
  await patchNeedProxy(name, $("ruleDomainNeed").value === "1");
}

export async function saveGlobalProxy() {
  const val = $("globalProxyInput").value.trim();
  state.globalProxy = val;
  /* 用户点过「保存」就是明确意志——**清空也算**（= 本局不用代理走直连）。
     旧逻辑 `proxyManual = !!val` 让清空后的标记复位成 false，补抓下一轮开跑时
     的自动探测（detectProxy fill）又把地址填回来：运行中怎么改/怎么清都弹回去，
     「没办法修改代理」的根子就在这。检测按钮仍会复位它（那也是用户明确动作）。 */
  state.proxyManual = true;
  setStatus(
    val
      ? `全局代理已设为 ${val}（自下一条抓取起生效）`
      : "已清除全局代理，改走直连（自动探测不再覆盖，自下一条抓取起生效）"
  );
}

/* 自动探测本地代理端口（7889-7899）；detectOnly 时只返回不写状态 */

export async function detectProxy({ fill = false, announce = true } = {}) {
  try {
    const data = await apiPost("/api/proxy-detect", {});
    const found = data.proxy || "";
    if (found) {
      if (fill) {
        state.globalProxy = found;
        state.proxyManual = false;   // 自动探测写入的值不算手填，之后仍可被下次探测刷新
        const input = $("globalProxyInput");
        if (input) input.value = found;
      }
      if (announce) setStatus(`检测到本地代理：${found}`);
    } else if (announce) {
      setStatus("7889-7899 区间未发现本地代理", "err");
    }
    return found;
  } catch (e) {
    if (announce) setStatus("代理探测失败：" + e.message, "err");
    return "";
  }
}

export async function addRule() {
  const pattern = $("rulePatternInput").value.trim();
  const need_proxy = $("ruleNeedSelect").value === "1";
  if (!pattern) { setStatus("请填写匹配模式", "err"); return; }
  $("rulePatternInput").value = "";
  await apiPost("/api/proxy-rule", { pattern, need_proxy });
  renderProxyPanel();
  loadProxyDomains(); // 模式规则变化 → 刷新侧边栏代理标记
  setStatus(`已添加规则：${pattern} → ${need_proxy ? "用代理" : "直连"}`);
}
