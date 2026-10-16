// 书签导入 / 导出：对齐 Raindrop 规则（HTML / CSV / TXT），前端为主。
//
// 数据模型映射（Almond 记录字段 → Raindrop 语义）：
//   - folder  ：根域名 record.domain（Almond 记录按域名分组，无子分组列）→ 作为集合名。
//   - note    ：Almond 无该字段 → CSV 列留空（诚实映射，不伪造）。
//   - created ：created_at（'YYYY-MM-DD HH:MM:SS' 本地）→ Unix 时间戳秒。
//   - tags    ：标签名逗号连接，多个用引号包裹。
// 导入时 folder/note/created 无法落库则忽略，只取 url/title/tags。

export function downloadFile(fileName, content, mime) {
  const blob = new Blob([content], { type: mime })
  const a = document.createElement('a')
  a.href = URL.createObjectURL(blob)
  a.download = fileName
  document.body.appendChild(a)
  a.click()
  a.remove()
  URL.revokeObjectURL(a.href)
}

const esc = (x) =>
  String(x ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;')

function folderOf(r) {
  return (r.domain || '').trim() || '未分类'
}

// 'YYYY-MM-DD HH:MM:SS'（本地）→ Unix 秒；无法解析返回 ''。
function toUnix(s) {
  if (!s) return ''
  const m = /^(\d{4})-(\d{2})-(\d{2})[ T](\d{2}):(\d{2}):(\d{2})$/.exec(s)
  if (!m) return ''
  const d = new Date(+m[1], +m[2] - 1, +m[3], +m[4], +m[5], +m[6])
  if (Number.isNaN(d.getTime())) return ''
  return Math.floor(d.getTime() / 1000)
}

function csvCell(v) {
  const s = String(v ?? '')
  return /[",\r\n]/.test(s) ? '"' + s.replace(/"/g, '""') + '"' : s
}

// RFC 4180 解析：支持引号包裹、内部双引号转义、字段内含逗号/换行。
function parseCsv(text) {
  const rows = []
  let row = []
  let field = ''
  let inQuotes = false
  for (let i = 0; i < text.length; i++) {
    const c = text[i]
    if (inQuotes) {
      if (c === '"') {
        if (text[i + 1] === '"') {
          field += '"'
          i++
        } else inQuotes = false
      } else field += c
    } else if (c === '"') inQuotes = true
    else if (c === ',') {
      row.push(field)
      field = ''
    } else if (c === '\n') {
      row.push(field)
      rows.push(row)
      row = []
      field = ''
    } else if (c !== '\r') field += c
  }
  row.push(field)
  rows.push(row)
  // 丢弃完全为空的尾部行（文件以换行结尾所致）
  return rows.filter((r) => !(r.length === 1 && r[0].trim() === ''))
}

// ---------------------------------------------------------------- 导出

function toTxt(records) {
  return records.map((r) => r.url).join('\n') + (records.length ? '\n' : '')
}

function toCsv(records) {
  const header = ['url', 'folder', 'title', 'note', 'tags', 'created']
  const body = records.map((r) => {
    const tags = (r.tags || []).map((t) => t.name).join(', ')
    return [r.url, folderOf(r), r.title || '', '', tags, toUnix(r.created_at)]
  })
  return [header, ...body].map((row) => row.map(csvCell).join(',')).join('\r\n') + '\r\n'
}

function toHtml(records) {
  const now = Math.floor(Date.now() / 1000)
  const byFolder = new Map()
  for (const r of records) {
    const f = folderOf(r)
    if (!byFolder.has(f)) byFolder.set(f, [])
    byFolder.get(f).push(r)
  }
  const items = (list) =>
    list
      .map((r) => {
        const tags = (r.tags || []).map((t) => t.name).join(',')
        const tagAttr = tags ? ` TAGS="${esc(tags)}"` : ''
        return `        <DT><A HREF="${esc(r.url)}" ADD_DATE="${now}"${tagAttr}>${esc(r.title || r.url)}</A>`
      })
      .join('\n')
  const folders = [...byFolder.entries()]
    .map(([f, list]) => `    <DT><H3>${esc(f)}</H3>\n    <DL><p>\n${items(list)}\n    </DL><p>`)
    .join('\n')
  return [
    '<!DOCTYPE NETSCAPE-Bookmark-file-1>',
    '<META HTTP-EQUIV="Content-Type" CONTENT="text/html; charset=UTF-8">',
    '<TITLE>Bookmarks</TITLE><H1>Bookmarks</H1><DL><p>',
    folders,
    '</DL><p>',
    '',
  ].join('\n')
}

// 返回 { content, mime, ext }，供 downloadFile 直接使用。
export function exportBookmarks(records, format) {
  if (format === 'csv') return { content: toCsv(records), mime: 'text/csv;charset=utf-8', ext: 'csv' }
  if (format === 'html') return { content: toHtml(records), mime: 'text/html;charset=utf-8', ext: 'html' }
  return { content: toTxt(records), mime: 'text/plain;charset=utf-8', ext: 'txt' }
}

// ---------------------------------------------------------------- 导入

// 解析上传文件为 items（结构同 /api/records/quick）：[{url, title?, tags?}]。
// 去重（按 url）与 http(s) 校验在内部完成；非 http(s) 行被丢弃。
export function parseImportFile(name, text) {
  text = String(text || '').replace(/^\uFEFF/, '') // 去 UTF-8 BOM（Excel/Raindrop 导出常见）
  const isHtml = /\.html?$/i.test(name)
  const isCsv = /\.csv$/i.test(name)
  const seen = new Set()
  const items = []
  const push = (it) => {
    const url = (it.url || '').trim()
    if (!/^https?:\/\//i.test(url) || seen.has(url)) return
    seen.add(url)
    items.push({ url, ...it })
  }
  if (isHtml) {
    const doc = new DOMParser().parseFromString(text, 'text/html')
    for (const a of doc.querySelectorAll('a[href]')) {
      push({
        url: a.href,
        title: (a.textContent || '').replace(/\s+/g, ' ').trim(),
        thumbnail: a.getAttribute('data-cover') || '',
        favicon: '',
        tags: (a.getAttribute('tags') || '').split(',').map((x) => x.trim()).filter(Boolean),
      })
    }
  } else if (isCsv) {
    const rows = parseCsv(text)
    if (rows.length < 2) return items // 仅表头或空文件
    const header = rows[0].map((h) => h.trim().toLowerCase())
    const iUrl = header.indexOf('url')
    const iTitle = header.indexOf('title')
    const iTags = header.indexOf('tags')
    if (iUrl < 0) return items // 缺 url 列无法导入
    for (let i = 1; i < rows.length; i++) {
      const row = rows[i]
      const url = (row[iUrl] || '').trim()
      if (!url) continue
      const tags = (row[iTags] || '')
        .split(',')
        .map((t) => t.trim())
        .filter(Boolean)
      push({ url, title: (row[iTitle] || '').trim(), tags })
    }
  } else {
    for (const line of text.split(/\r?\n/)) push({ url: line.trim() })
  }
  return items
}
