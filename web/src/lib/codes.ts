/**
 * 番号 + 下载链接解析：与后端 backend/codes.py 保持同一套规则。
 * 只用于提交前的即时预览，最终以服务端解析为准。
 */
const DASHES = '-‐‑‒–—－'
const CODE_RE = new RegExp(`[A-Za-z]{2,6}[${DASHES}]\\d{2,6}`, 'g')
const DASH_RE = new RegExp(`[${DASHES}]`, 'g')

// ed2k 可能含空格，用非贪婪匹配到结尾的 |/；其余链接遇到空白即止
const LINK_RE = new RegExp(
  [
    'ed2k://\\|[^\\r\\n]*?\\|/',
    'magnet:\\?[^\\s"\'<>]+',
    'thunder://[^\\s"\'<>]+',
    '(?:https?|ftp)://[^\\s"\'<>，。；、）】」》]+'
  ].join('|'),
  'gi'
)

const TRAILING_RE = /[.,;:!?)\]}>"'，。；：！？）】》」]+$/

export interface ParsedEntry {
  code: string
  link: string | null
  line: string
}

export interface ProblemLine {
  line: string
  code?: string
  link?: string
}

export interface ParseSummary {
  entries: ParsedEntry[]
  codes: string[]
  count: number
  withLink: number
  noLink: ProblemLine[]
  onlyLink: ProblemLine[]
  unknown: ProblemLine[]
}

export function normalizeCode(raw: string): string {
  return raw.replace(DASH_RE, '-').toUpperCase()
}

export function findCodes(line: string): string[] {
  return [...line.matchAll(CODE_RE)].map((m) => normalizeCode(m[0]))
}

export function findLinks(line: string): string[] {
  const out: string[] = []
  for (const m of line.matchAll(LINK_RE)) {
    const link = m[0].replace(TRAILING_RE, '')
    if (link && !out.includes(link)) out.push(link)
  }
  return out
}

/** 只保留番号列表（老接口，保留给已有调用方） */
export function parseCodes(text: string): string[] {
  if (!text) return []
  const seen = new Set<string>()
  const out: string[] = []
  for (const match of text.matchAll(CODE_RE)) {
    const code = normalizeCode(match[0])
    if (!seen.has(code)) {
      seen.add(code)
      out.push(code)
    }
  }
  return out
}

/** 按行解析整段文本，分类给出「正常 / 缺链接 / 只有链接 / 未识别」 */
export function parseText(text: string): ParseSummary {
  const lines = (text || '')
    .split(/\r\n|\n|\r/)
    .map((l) => l.trim())
    .filter(Boolean)
    .map((line) => ({ line, codes: findCodes(line), links: findLinks(line) }))

  const entries: ParsedEntry[] = []
  const byCode = new Map<string, ParsedEntry>()

  for (const row of lines) {
    if (!row.codes.length) continue
    const link = row.links[0] ?? null
    for (const code of row.codes) {
      const existing = byCode.get(code)
      if (!existing) {
        const entry: ParsedEntry = { code, link, line: row.line }
        byCode.set(code, entry)
        entries.push(entry)
      } else if (!existing.link && link) {
        existing.link = link
      }
    }
  }

  const noLink: ProblemLine[] = []
  const onlyLink: ProblemLine[] = []
  const unknown: ProblemLine[] = []

  for (const row of lines) {
    if (row.codes.length) {
      if (!row.links.length && row.codes.some((c) => !byCode.get(c)?.link)) {
        noLink.push({ line: row.line, code: row.codes[0] })
      }
    } else if (row.links.length) {
      onlyLink.push({ line: row.line, link: row.links[0] })
    } else {
      unknown.push({ line: row.line })
    }
  }

  return {
    entries,
    codes: entries.map((e) => e.code),
    count: entries.length,
    withLink: entries.filter((e) => !!e.link).length,
    noLink,
    onlyLink,
    unknown
  }
}
