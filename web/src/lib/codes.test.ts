import { describe, expect, it } from 'vitest'
import { findLinks, parseCodes, parseText } from './codes'

const ED2K = 'ed2k://|file|@IPZ-918_restored.mp4|10174562190|ABC|/'

describe('parseCodes', () => {
  it('解析示例文本并保持顺序', () => {
    expect(parseCodes('IPX-528 田中レモン AVOP-201 相沢みなみ')).toEqual([
      'IPX-528',
      'AVOP-201'
    ])
  })

  it('大小写归一 + 去重', () => {
    expect(parseCodes('ipx-528 IPX-528 IpX-528')).toEqual(['IPX-528'])
  })

  it('支持全角连字符', () => {
    expect(parseCodes('IPX－528')).toEqual(['IPX-528'])
  })

  it('忽略日期与磁力哈希', () => {
    expect(parseCodes('2024-11-03 F91AF5AFD93EBE9D9FDD390AB00E3C1334C8AA69')).toEqual([])
  })

  it('空文本返回空数组', () => {
    expect(parseCodes('')).toEqual([])
  })

  it('ed2k 链接列表（test.txt 场景）', () => {
    const text = [
      'ed2k://|file|@IPZ-918_restored.mp4|10174562190|ABC|/',
      'ed2k://|file|MEYD-263-xxx.mkv|3408696450|DEF|/',
      'magnet:?xt=urn:btih:F91AF5AFD93EBE9D9FDD390AB00E3C1334C8AA69'
    ].join('\n')
    expect(parseCodes(text)).toEqual(['IPZ-918', 'MEYD-263'])
  })
})

describe('findLinks', () => {
  it('ed2k 链接（含中文文件名）完整识别', () => {
    const line = 'ed2k://|file|MEYD-263-破解-中文.mkv|3408696450|A676E092E7C14407E62501499CDFF851|/'
    expect(findLinks(line)).toEqual([line])
  })

  it('不把中文标点吃进链接', () => {
    expect(findLinks('见 https://example.com/a.mp4，谢谢')).toEqual([
      'https://example.com/a.mp4'
    ])
  })

  it('没有链接时返回空数组', () => {
    expect(findLinks('IPX-528 田中レモン')).toEqual([])
  })
})

describe('parseText', () => {
  it('每行一个条目，链接存进对应番号', () => {
    const r = parseText(`${ED2K}\nIPX-528`)
    expect(r.count).toBe(2)
    expect(r.withLink).toBe(1)
    expect(r.entries[0]).toMatchObject({ code: 'IPZ-918', link: ED2K })
    expect(r.entries[1]).toMatchObject({ code: 'IPX-528', link: null })
  })

  it('分类三类问题行', () => {
    const r = parseText(
      [ED2K, 'IPX-529 田中', 'magnet:?xt=urn:btih:ABC', '什么都没有'].join('\n')
    )
    expect(r.count).toBe(2)
    expect(r.withLink).toBe(1)
    expect(r.noLink).toEqual([{ line: 'IPX-529 田中', code: 'IPX-529' }])
    expect(r.onlyLink).toEqual([
      { line: 'magnet:?xt=urn:btih:ABC', link: 'magnet:?xt=urn:btih:ABC' }
    ])
    expect(r.unknown).toEqual([{ line: '什么都没有' }])
  })

  it('重复番号第二次带链接 → 补到第一次，且不再报缺链接', () => {
    const r = parseText(`IPX-528\n${ED2K.replace('IPZ-918', 'IPX-528')}`)
    expect(r.count).toBe(1)
    expect(r.withLink).toBe(1)
    expect(r.noLink).toEqual([])
  })

  it('忽略空行', () => {
    const r = parseText('\n\n  \nIPX-528\n\n')
    expect(r.count).toBe(1)
    expect(r.unknown).toEqual([])
  })

  it('空文本', () => {
    expect(parseText('').count).toBe(0)
  })
})
