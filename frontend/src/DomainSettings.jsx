import { useState } from 'react'
import { apiPatch, apiPost } from './api.js'

/* 三态选择：value "" = 跟随全局 / "1" = 开 / "0" = 关（对应后端的 null / true / false） */
function TriSelect({ value, onChange, follow, on, off }) {
  return (
    <select className="input" value={value} onChange={(e) => onChange(e.target.value)}>
      <option value="">{follow}</option>
      <option value="1">{on}</option>
      <option value="0">{off}</option>
    </select>
  )
}

function Field({ label, hint, children }) {
  return (
    <div className="dp-field">
      <label className="dp-label">{label}</label>
      {children}
      {hint && <div className="dp-hint">{hint}</div>}
    </div>
  )
}

const MODE_OPTIONS = [
  { value: '', text: '标签: 值行（默认）' },
  { value: 'tags', text: '标签链（每个匹配元素即一个标签）' },
  { value: 'og', text: 'OG 快速模式（仅标题+封面，纯静态）' },
]

export default function DomainSettings({ domain, cfg, onSaved }) {
  const [alias, setAlias] = useState(cfg.display_name || '')
  const [proxy, setProxy] = useState(cfg.need_proxy == null ? '' : cfg.need_proxy ? '1' : '0')
  const [auto, setAuto] = useState(cfg.auto_fetch == null ? '' : cfg.auto_fetch ? '1' : '0')
  const [cover, setCover] = useState(cfg.cover == null ? '' : cfg.cover ? '1' : '0')
  const [mode, setMode] = useState(cfg.detail_mode === 'tags' ? 'tags' : cfg.detail_mode === 'og' ? 'og' : '')
  const [selector, setSelector] = useState(cfg.detail_selector || '')
  const [resetTo, setResetTo] = useState('')
  const [status, setStatus] = useState(null)

  const selectorHint =
    mode === 'tags'
      ? '标签链模式没有默认选择器——留空 = 不提取详情。B 站视频页可用 .tag-panel .tag-link' +
        '（音乐/话题/普通标签全在内，「展开更多」按钮不是链、不会被误抓）'
      : mode === 'og'
        ? 'OG 快速模式只抓静态 HTML 的 head 里的 og:title / og:image，纯静态、不起浏览器、' +
          '不解析正文详情，此选择器不生效'
        : '按站点覆盖默认的 .space-y-2 > *；选择器无效时详情为空（不报错），同样对之后的抓取生效'

  async function save() {
    setStatus({ kind: 'busy', text: `保存 ${domain} 的域名设置…` })
    const payload = {
      name: domain,
      display_name: alias.trim(),
      need_proxy: proxy === '' ? null : proxy === '1',
      auto_fetch: auto === '' ? null : auto === '1',
      cover: cover === '' ? null : cover === '1',
      detail_selector: selector.trim(),
      detail_mode: mode === 'tags' ? 'tags' : mode === 'og' ? 'og' : '',
    }
    try {
      const data = await apiPatch('/api/domain', payload)
      if (!data.ok) {
        setStatus({ kind: 'err', text: '保存失败：' + data.error })
        return
      }
      const np = data.domain ? data.domain.need_proxy : undefined
      const label = np == null ? '跟随全局' : np ? '用代理' : '直连'
      setStatus({ kind: 'ok', text: `已保存 ${domain} 的设置（${label}）` })
      if (onSaved) onSaved(data.domain)
    } catch (e) {
      setStatus({ kind: 'err', text: '保存失败：' + e.message })
    }
  }

  async function reset() {
    const next = resetTo.trim()
    if (!next) {
      setStatus({ kind: 'err', text: '请填写新域名' })
      return
    }
    if (!window.confirm(`确定把 ${domain} 全部替换为 ${next}？\n记录链接、封面、域名规则与代理规则都会改写，不可撤销。`)) {
      return
    }
    setStatus({ kind: 'busy', text: `域名重置 ${domain} → ${next}…` })
    try {
      const data = await apiPost('/api/domain/replace', { old: domain, new: next })
      if (!data.ok) {
        setStatus({ kind: 'err', text: '域名重置失败（未改动任何数据）：' + data.error })
        return
      }
      const total = data.total || 0
      const failed = data.failed || 0
      const rules = data.rules || 0
      if (!total) {
        setStatus(
          rules
            ? { kind: 'ok', text: `域名重置 ${data.old} → ${data.new}：命中 0 条记录，改写规则 ${rules} 条` }
            : { kind: 'err', text: `域名重置 ${data.old} → ${data.new}：未找到任何包含该域名的记录，0 条改动` },
        )
        return
      }
      setStatus({
        kind: failed ? 'err' : 'ok',
        text:
          `域名重置 ${data.old} → ${data.new}：共 ${total} 条，成功 ${data.records}` +
          `，重复 ${data.merged || 0}，失败 ${failed}` +
          (rules ? `，改写规则 ${rules} 条` : '') +
          (failed && data.error ? `（${data.error}）` : ''),
      })
    } catch (e) {
      setStatus({ kind: 'err', text: '域名重置失败：' + e.message })
    }
  }

  return (
    <div className="ds-card">
      <h2>{domain}</h2>

      <Field label="显示别名">
        <input
          className="input"
          value={alias}
          spellCheck={false}
          placeholder="显示别名，如：深度求索"
          onChange={(e) => setAlias(e.target.value)}
        />
      </Field>

      <Field label="代理">
        <TriSelect
          value={proxy}
          onChange={setProxy}
          follow="代理：跟随全局"
          on="代理：强制走代理"
          off="代理：强制直连"
        />
      </Field>

      <Field label="自动补抓">
        <TriSelect
          value={auto}
          onChange={setAuto}
          follow="自动补抓：跟随全局"
          on="自动补抓：开"
          off="自动补抓：关（补抓不碰这个域名）"
        />
      </Field>

      <Field
        label="封面来源"
        hint="默认取 og:image；「不要封面」抓完后清空封面（显示字母占位），「只要图标」用站点图标当封面。对**之后的重新抓取**生效"
      >
        <TriSelect
          value={cover}
          onChange={setCover}
          follow="封面：默认（og:image）"
          on="封面：只要图标"
          off="封面：不要封面"
        />
      </Field>

      <Field
        label="详情提取模式"
        hint={
          '「标签: 值行」解析「发行日期: 2020-01-01」这类结构（默认选择器 .space-y-2 > *）；' +
          '「标签链」解析扁平标签列表（如 B 站视频页的标签面板）；' +
          '「OG 快速模式」只抓标题+封面、纯静态不起浏览器，最快。只影响之后的抓取'
        }
      >
        <select className="input" value={mode} onChange={(e) => setMode(e.target.value)}>
          {MODE_OPTIONS.map((o) => (
            <option key={o.value} value={o.value}>
              {o.text}
            </option>
          ))}
        </select>
      </Field>

      <Field label="详情字段选择器" hint={selectorHint}>
        <input
          className="input"
          value={selector}
          spellCheck={false}
          placeholder={
            mode === 'tags'
              ? '必填：每个标签元素的选择器，如 .tag-panel .tag-link'
              : mode === 'og'
                ? 'OG 模式忽略此选择器'
                : '留空用默认 .space-y-2 > *'
          }
          onChange={(e) => setSelector(e.target.value)}
        />
      </Field>

      <Field label={`域名重置：${domain}（含其子域名，不可撤销）`} hint="记录链接、封面、域名规则与代理规则都会改写">
        <div className="proxy-row">
          <input
            className="input"
            value={resetTo}
            spellCheck={false}
            placeholder="新域名，如 example.com"
            onChange={(e) => setResetTo(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter') {
                e.preventDefault()
                reset()
              }
            }}
          />
          <button className="btn-danger" onClick={reset}>
            替换
          </button>
        </div>
      </Field>

      <div className="ds-actions">
        <button className="btn-primary" onClick={save}>
          保存
        </button>
      </div>

      {status && <div className={`ds-status ds-${status.kind}`}>{status.text}</div>}
    </div>
  )
}
