// @vitest-environment happy-dom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import FilmTree from './FilmTree.vue'
import type { FilmDir, FilmEntry } from '../api'

const DIRS: FilmDir[] = [
  { id: 1, path: '/data/films/a', label: 'a', valid: 1 },
  { id: 2, path: '/data/films/b', label: 'b', valid: 0 }
]

function dirEntry(id: number, name: string, parent: string): FilmEntry {
  return {
    id,
    name,
    rel_path: parent ? `${parent}/${name}` : name,
    parent_rel: parent,
    is_dir: true,
    size: 0,
    mtime: 0,
    code: null,
    thumb_rel: null,
    local_path: null,
    video_count: 0,
    collage: []
  }
}

function fileEntry(id: number, name: string, parent: string): FilmEntry {
  return {
    id,
    name,
    rel_path: parent ? `${parent}/${name}` : name,
    parent_rel: parent,
    is_dir: false,
    size: 0,
    mtime: 0,
    code: null,
    thumb_rel: null,
    local_path: null
  }
}

// 目录树：a/sub1/deep、a/sub2（空）；文件不该出现在树里
const LEVELS: Record<string, FilmEntry[]> = {
  '': [dirEntry(11, 'sub1', ''), dirEntry(12, 'sub2', ''), fileEntry(13, 'root.mp4', '')],
  sub1: [dirEntry(21, 'deep', 'sub1'), fileEntry(22, 'x.mp4', 'sub1')],
  'sub1/deep': [fileEntry(31, 'y.mp4', 'sub1/deep')],
  sub2: []
}

const fetchMock = vi.fn()

function jsonRes(body: unknown) {
  return new Response(JSON.stringify(body), { status: 200 })
}

beforeEach(() => {
  fetchMock.mockReset()
  vi.stubGlobal('fetch', fetchMock)
  fetchMock.mockImplementation(async (url: string) => {
    const u = new URL(String(url), 'http://localhost')
    const path = u.searchParams.get('path') ?? ''
    return jsonRes({
      dir: DIRS[0],
      path,
      scanning: false,
      entries: LEVELS[path] ?? []
    })
  })
})

afterEach(() => {
  vi.unstubAllGlobals()
})

function mountTree(props: Record<string, unknown> = {}) {
  return mount(FilmTree, {
    props: { dirs: DIRS, currentDirId: null, currentPath: '', refreshTick: 0, ...props }
  })
}

/** 按显示名找到对应的树行 */
function rowByName(w: ReturnType<typeof mountTree>, text: string) {
  return w.findAll('.tree-row').find((r) => r.find('.tree-name').text().includes(text))!
}

describe('FilmTree', () => {
  it('渲染根目录列表；没有当前目录时不发请求', () => {
    const w = mountTree()
    const rows = w.findAll('.tree-row.root')
    expect(rows.length).toBe(2)
    expect(rows[0].text()).toContain('a')
    expect(rows[1].text()).toContain('b')
    expect(fetchMock).not.toHaveBeenCalled()
  })

  it('展开根节点：请求该级，且只显示文件夹', async () => {
    const w = mountTree()
    await w.find('.tree-toggle').trigger('click')
    await flushPromises()

    expect(fetchMock).toHaveBeenCalledTimes(1)
    const called = new URL(String(fetchMock.mock.calls[0][0]), 'http://localhost')
    expect(called.searchParams.get('dir_id')).toBe('1')
    const names = w.findAll('.tree-name').map((n) => n.text())
    expect(names.some((t) => t.includes('sub1'))).toBe(true)
    expect(names.some((t) => t.includes('sub2'))).toBe(true)
    expect(names.some((t) => t.includes('root.mp4'))).toBe(false)
  })

  it('可逐级展开到任意深度，空文件夹显示占位行', async () => {
    const w = mountTree()
    await w.find('.tree-toggle').trigger('click')
    await flushPromises()

    // 展开 sub1
    await rowByName(w, 'sub1').find('.tree-toggle').trigger('click')
    await flushPromises()
    expect(w.text()).toContain('deep')
    expect(w.text()).not.toContain('x.mp4') // 文件不进树

    // 展开 deep：没有子文件夹 → 占位
    await rowByName(w, 'deep').find('.tree-toggle').trigger('click')
    await flushPromises()
    expect(w.find('.tree-row.placeholder').exists()).toBe(true)
    expect(w.find('.tree-row.placeholder').text()).toContain('没有子文件夹')
  })

  it('点击行发出 select(dirId, path)', async () => {
    const w = mountTree()
    await w.findAll('.tree-row.root')[0].trigger('click')
    expect(w.emitted('select')![0]).toEqual([1, ''])

    await w.findAll('.tree-row.root')[0].find('.tree-toggle').trigger('click') // 展开 a
    await flushPromises()
    await rowByName(w, 'sub1').trigger('click')
    expect(w.emitted('select')![1]).toEqual([1, 'sub1'])
  })

  it('切换目录时自动展开祖先并高亮当前行', async () => {
    const w = mountTree({ currentDirId: 1, currentPath: '' })
    await flushPromises()
    expect(w.findAll('.tree-row.active')[0].text()).toContain('a')

    await w.setProps({ currentPath: 'sub1/deep' })
    await flushPromises()
    await flushPromises()

    const active = w.find('.tree-row.active')
    expect(active.text()).toContain('deep')
    const names = w.findAll('.tree-name').map((n) => n.text())
    expect(names.some((t) => t.includes('sub1'))).toBe(true)
    expect(names.some((t) => t.includes('deep'))).toBe(true)
  })

  it('收到指向已展开级的 scan 事件时，直接用载荷更新子级', async () => {
    const w = mountTree({ currentDirId: 1, currentPath: '' })
    await flushPromises()

    w.setProps({
      scanEvent: {
        dirId: 1,
        path: '',
        entries: [
          dirEntry(11, 'sub1', ''),
          dirEntry(12, 'sub2', ''),
          dirEntry(14, 'sub3', '')
        ],
        seq: 1
      }
    })
    await flushPromises()

    const names = w.findAll('.tree-name').map((n) => n.text())
    expect(names.filter((t) => t.includes('sub3')).length).toBe(1)
  })

  it('scan 事件指向未展开的级时不生效', async () => {
    const w = mountTree()
    w.setProps({
      scanEvent: { dirId: 1, path: 'not-expanded', entries: [], seq: 1 }
    })
    await flushPromises()
    expect(fetchMock).not.toHaveBeenCalled()
    expect(w.findAll('.tree-row').length).toBe(2)
  })
})
