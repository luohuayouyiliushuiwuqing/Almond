/* 域名管理唯一实现：子页设置区与代理面板弹窗共用。
   字段控件、保存 PATCH、域名重置（二次确认 + 计数状态栏）只在这里写一份；
   两个入口只决定「显示哪些字段」与「保存/重置后干什么」（opts.onDone / opts.onResetDone）。 */
import { apiPatch, apiPost } from "./api.js";
import { reloadAll } from "./records.js";
import { el, setStatus } from "./state.js";

/* 三态 select 的统一构造：null=跟随全局/默认，true=1，false=0 */
function triSelect(options, value) {
  const sel = el("select", "input");
  sel.innerHTML = options
    .map(([v, text]) => `<option value="${v}">${text}</option>`)
    .join("");
  sel.value = value === null || value === undefined ? "" : (value ? "1" : "0");
  return sel;
}

function fieldWrap(labelText, node, hint) {
  const wrap = el("div", "dp-field");
  const label = el("label", null, labelText);
  label.appendChild(node);
  wrap.appendChild(label);
  if (hint) wrap.appendChild(el("div", "dp-hint", hint));
  return wrap;
}

/** 构建域名设置表单。
 *  opts.fields ⊆ ["alias","proxy","auto","cover","selector","reset"]（默认全开）；
 *  opts.compact 适配弹窗窄布局；opts.onResetDone(data) 重置成功后的入口收尾。
 *  返回的 box 带 `box.__collect()` → PATCH /api/domain 的字段集（按 fields 裁剪）。
 */
export function buildSettingsForm(domain, cfg, opts = {}) {
  const fields = opts.fields || ["alias", "proxy", "auto", "cover", "selector", "reset"];
  const box = el("div", opts.compact ? "dp-settings dp-compact" : "dp-settings");
  const parts = {};

  if (fields.includes("alias")) {
    parts.alias = el("input", "input");
    parts.alias.value = cfg.display_name || "";
    parts.alias.placeholder = "显示别名，如：深度求索";
    parts.alias.spellcheck = false;
    box.appendChild(fieldWrap("显示别名", parts.alias));
  }
  if (fields.includes("proxy")) {
    parts.proxy = triSelect(
      [["", "代理：跟随全局"], ["1", "代理：强制走代理"], ["0", "代理：强制直连"]],
      cfg.need_proxy,
    );
    box.appendChild(fieldWrap("代理", parts.proxy));
  }
  if (fields.includes("auto")) {
    parts.auto = triSelect(
      [["", "自动补抓：跟随全局"], ["1", "自动补抓：开"], ["0", "自动补抓：关（补抓不碰这个域名）"]],
      cfg.auto_fetch,
    );
    box.appendChild(fieldWrap("自动补抓", parts.auto));
  }
  if (fields.includes("cover")) {
    parts.cover = triSelect(
      [["", "封面：默认（og:image）"], ["0", "封面：不要封面"], ["1", "封面：只要图标"]],
      cfg.cover,
    );
    box.appendChild(fieldWrap("封面来源", parts.cover,
      "默认取 og:image；「不要封面」抓完后清空封面（显示字母占位），「只要图标」用站点图标当封面。对**之后的重新抓取**生效"));
  }
  if (fields.includes("selector")) {
    parts.selector = el("input", "input");
    parts.selector.value = cfg.detail_selector || "";
    parts.selector.placeholder = "留空用默认 .space-y-2 > *";
    parts.selector.spellcheck = false;
    box.appendChild(fieldWrap("详情字段选择器", parts.selector,
      "按站点覆盖默认的 .space-y-2 > *；选择器无效时详情为空（不报错），同样对之后的抓取生效"));
  }
  if (fields.includes("reset")) {
    /* 域名重置：危险操作单独一行 + 二次确认（输入行自带触发按钮，两个入口同款） */
    const input = el("input", "input");
    input.placeholder = "新域名，如 example.com";
    input.spellcheck = false;
    parts.resetInput = input;
    const btn = el("button", "btn-danger", "替换");
    const fire = () => resetDomainTo(domain, input, opts.onResetDone);
    btn.addEventListener("click", fire);
    input.addEventListener("keydown", (e) => {
      if (e.key === "Enter") { e.preventDefault(); fire(); }
    });
    const line = el("div", "proxy-row");
    line.append(input, btn);
    box.appendChild(fieldWrap(`域名重置：${domain}（含其子域名，不可撤销）`, line,
      "记录链接、封面、域名规则与代理规则都会改写"));
  }

  box.__collect = () => {
    const out = { name: domain };
    if (parts.alias) out.display_name = parts.alias.value.trim();
    if (parts.proxy) out.need_proxy = parts.proxy.value === "" ? null : parts.proxy.value === "1";
    if (parts.auto) out.auto_fetch = parts.auto.value === "" ? null : parts.auto.value === "1";
    if (parts.cover) out.cover = parts.cover.value === "" ? null : parts.cover.value === "1";
    if (parts.selector) out.detail_selector = parts.selector.value.trim();
    return out;
  };
  return box;
}

/** 保存域名设置：PATCH → 三刷 → onDone（入口收尾）→ 状态栏。 */
export async function saveDomainSettings(domain, box, opts = {}) {
  setStatus(`保存 ${domain} 的域名设置…`, "busy");
  try {
    const data = await apiPatch("/api/domain", box.__collect());
    if (!data.ok) { setStatus("保存失败：" + data.error, "err"); return; }
    await reloadAll();
    if (opts.onDone) opts.onDone(data);
    const np = data.domain ? data.domain.need_proxy : undefined;
    const label = np === null || np === undefined ? "跟随全局" : (np ? "用代理" : "直连");
    setStatus(`已保存 ${domain} 的设置（${label}）`);
  } catch (e) {
    setStatus("保存失败：" + e.message, "err");
  }
}

/** 域名重置：confirm → POST /api/domain/replace → onDone → 三刷 → 计数状态栏。
 *  服务端单事务：走到失败分支必然是整体回滚，没有半截改动。
 */
export async function resetDomainTo(old, inputEl, onDone) {
  const next = (inputEl.value || "").trim();
  if (!next) { setStatus("请填写新域名", "err"); inputEl.focus(); return; }
  if (!confirm(`确定把 ${old} 全部替换为 ${next}？\n记录链接、封面、域名规则与代理规则都会改写，不可撤销。`)) return;
  setStatus(`域名重置 ${old} → ${next}…`, "busy");
  try {
    const data = await apiPost("/api/domain/replace", { old, new: next });
    if (!data.ok) {
      setStatus("域名重置失败（未改动任何数据）：" + data.error, "err");
      return;
    }
    if (onDone) onDone(data);
    await reloadAll();
    const total = data.total || 0;
    const failed = data.failed || 0;
    const rules = data.rules || 0;
    if (!total) {
      // 一条都没命中：多半是旧域名早就被清理过，别让人以为改了什么
      setStatus(
        rules
          ? `域名重置 ${data.old} → ${data.new}：命中 0 条记录，改写规则 ${rules} 条`
          : `域名重置 ${data.old} → ${data.new}：未找到任何包含该域名的记录，0 条改动`,
        rules ? undefined : "err"
      );
      return;
    }
    setStatus(
      `域名重置 ${data.old} → ${data.new}：共 ${total} 条，成功 ${data.records}` +
      `，重复 ${data.merged || 0}，失败 ${failed}` +
      (rules ? `，改写规则 ${rules} 条` : "") +
      (failed && data.error ? `（${data.error}）` : ""),
      failed ? "err" : undefined
    );
  } catch (e) {
    setStatus("域名重置失败：" + e.message, "err");
  }
}
