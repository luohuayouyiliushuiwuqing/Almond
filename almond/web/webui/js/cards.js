/* 卡片 DOM：建骨架 + 就地补丁（fillCard/updateCard）。 */
import { emit } from "./bus.js";
import { openDetail } from "./detail.js";
import { updateDomainRow } from "./domain-page.js";
import { fetchRecord } from "./fetch.js";
import { openRename, openTag } from "./modals.js";
import { matchesFilter, removeRecord } from "./records.js";
import { el, hostOf, state } from "./state.js";

function imgSrc(u) {
  const proxy = state.globalProxy;
  return "/api/img?src=" + encodeURIComponent(u) +
    (proxy ? "&proxy=" + encodeURIComponent(proxy) : "");
}

/* —— 卡片 / 列表行共用解剖件（domain-page.buildRow 也用；DOM 类名与选择器不变）—— */

export function statusDotClass(record) {
  return !record.fetched ? "pending" : record.success ? "ok" : "fail";
}

export function statusDotTitle(record) {
  return !record.fetched ? "还没抓取" : record.success ? "抓取成功" : "抓取失败";
}

export function makeTagBadges(tags) {
  return (tags || []).map((t) => el("span", "tag-badge", t.name));
}

export function makeCheckbox(record, host) {
  /* 复选框（多选）：常驻半透明，选中高亮。命中靠 z-index 压过 a.open 的 inset:0。
     只读 state 恢复勾选态（懒建分组后补的卡片也要对）；change 走 #board 上的
     事件委托（selectbar.js 的 initSelectBar），所以既不加监听、也不 import selectbar。
     ⚠ el() 不设属性，input 默认是 text，type 必须手动指定 */
  const cb = el("input", "card-check");
  cb.type = "checkbox";
  cb.checked = state.selectedUrls.has(record.url);
  cb.title = record.url;
  host.classList.toggle("selected", cb.checked);
  return cb;
}

export function makeRecordActions(host, layout) {
  /* 操作按钮组：闭包一律取宿主的 __record（fillCard/补丁会更新它）。
     layout="card" → 两组（缩略图右上 详情/重抓 + 底部 改名/标签/删除）；
     layout="row"  → 一组（dp-acts：详情/重抓/改名/标签/删）。
     按钮是 <a.open> 的兄弟节点，preventDefault/stopPropagation 防跳转。 */
  const rec = () => host.__record;
  const mk = (labelText, title, fn) => {
    const b = el("button", null, labelText);
    if (title) b.title = title;
    b.addEventListener("click", (e) => { e.preventDefault(); e.stopPropagation(); fn(); });
    return b;
  };
  if (layout === "row") {
    const acts = el("div", "dp-acts");
    acts.append(
      mk("详情", "查看详情字段", () => openDetail(rec())),
      mk("重抓", "重新抓取这条", () => fetchRecord(rec().url)),
      mk("改名", "重命名", () => openRename(rec())),
      mk("标签", "给这一条打标签", () => openTag(rec())),
      mk("删", "删除这条记录", () => removeRecord(rec())),
    );
    for (const b of acts.children) b.classList.add("dp-act");
    return acts;
  }
  const topActions = el("div", "thumb-actions");
  topActions.append(
    mk("详情", null, () => openDetail(rec())),
    mk("重新抓取", null, () => fetchRecord(rec().url)),
  );
  const actions = el("div", "actions");
  actions.append(
    mk("重命名", null, () => openRename(rec())),
    mk("标签", null, () => openTag(rec())),
    mk("删除", null, () => removeRecord(rec())),
  );
  return [topActions, actions];
}

/* src 没变就绝不碰 <img> 节点——换节点要重新解码，肉眼就是一闪白 */

function setCover(open, record) {
  let img = open.querySelector(".cover");
  if (!record.thumbnail) { if (img) img.remove(); return; }
  const src = imgSrc(record.thumbnail);
  if (!img) {
    img = new Image();
    img.className = "cover";
    img.loading = "lazy";
    img.onerror = () => img.remove();
    open.insertBefore(img, open.querySelector(".favicon"));  // 封面要压在 favicon 下面
  }
  if (img.getAttribute("src") !== src) img.src = src;
}

function setFavicon(open, record) {
  let fav = open.querySelector(".favicon");
  const want = record.favicon && record.favicon.startsWith("http") ? record.favicon : "";
  if (!want) { if (fav) fav.remove(); return; }
  const src = imgSrc(want);
  if (!fav) {
    fav = new Image();
    fav.className = "favicon";
    fav.loading = "lazy";
    fav.onerror = () => fav.remove();
    open.appendChild(fav);   // 角标在最上层
  }
  if (fav.getAttribute("src") !== src) fav.src = src;
}

/* 卡片里会随抓取结果变化的部分。抓取完成、改名、批量提取都只动这些节点，
   不重建整张卡也不重绘看板——原先每次都 render()，board.innerHTML="" 把
   几百张卡连同图片全部拔掉再插回去，就是「抓到数据页面闪一下」的来源 */

function fillCard(card, record) {
  card.__record = record;   // 按钮闭包从这里取最新记录，别用建卡时的旧对象

  const title = card.querySelector(".title");
  title.textContent = record.title || "（标题待补充）";   // 没标题就灰着，别装作有标题
  title.classList.toggle("pending", !record.title);

  card.querySelector(".dot").className = "dot " + statusDotClass(record);

  const open = card.querySelector(".open");
  setCover(open, record);
  setFavicon(open, record);

  const row = card.querySelector(".domain");
  for (const b of row.querySelectorAll(".tag-badge")) b.remove();
  row.append(...makeTagBadges(record.tags));
}

/* 单条记录更新：就地打补丁，不整页重绘 */

export function updateCard(record) {
  const card = document.querySelector(`#board .card[data-url="${CSS.escape(record.url)}"]`);
  if (!card) {
    /* 不在看板卡片上 → 试试域名子页的列表行（两个视图互斥，最多只有一个存在）。
       两者都没有（懒建分组还没建卡）就是 no-op，建的时候自然用最新数据 */
    updateDomainRow(record);
    return;
  }
  if (!matchesFilter(record)) {     // 改名/抓取后掉出当前筛选 → 只能整页重排
    emit("render");
    return;
  }
  fillCard(card, record);
}

export function createCard(record) {
  const card = el("div", "card");
  card.dataset.url = record.url;    // 单条更新按这个定位
  const host = hostOf(record.url);

  /* 缩略图：图 → 字母占位；可选 favicon 角标（图片走服务端代理加载）。
     打开链接用内部 <a>，操作按钮是它的兄弟节点，避免点按钮时触发跳转。
     封面/favicon 与标题、标签一样交给末尾的 fillCard —— 抓取回来才有。 */
  const thumb = el("div", "thumb");

  /* 复选框（多选）与操作按钮都用共用解剖件（与子页列表行同款） */
  thumb.appendChild(makeCheckbox(record, card));

  const open = el("a", "open");
  open.href = record.url;
  open.target = "_blank";
  open.rel = "noopener";
  open.title = record.url;
  open.appendChild(el("span", "letter", host.slice(0, 1).toUpperCase()));
  thumb.appendChild(open);

  /* 按钮闭包一律取 card.__record（fillCard 会更新它）：
     否则就地补丁后，点「详情/重命名」拿到的还是建卡那一刻的旧数据 */
  thumb.append(...makeRecordActions(card, "card"));

  /* 文本区：结构建一次，标题/状态点/徽标由 fillCard 填 */
  const body = el("div", "body");
  const title = el("a", "title");
  title.href = record.url;
  title.target = "_blank";
  title.rel = "noopener";
  const domainRow = el("div", "domain");
  domainRow.appendChild(el("span", "dot"));
  domainRow.appendChild(el("span", null, host));
  body.append(title, domainRow);

  card.append(thumb, body);
  fillCard(card, record);
  return card;
}
