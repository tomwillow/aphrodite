// @vitest-environment happy-dom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import FilmTree from './FilmTree.vue'
import type { FilmDir } from '../api'

const DIRS: FilmDir[] = [
  { id: 1, path: '/data/films/a', label: 'a', valid: 1 },
  { id: 2, path: '/data/films/b', label: 'b', valid: 0 }
]

const fetchMock = vi.fn()

function jsonRes(body: unknown) {
  return new Response(JSON.stringify(body), { status: 200 })
}

beforeEach(() => {
  fetchMock.mockReset()
  vi.stubGlobal('fetch', fetchMock)
  fetchMock.mockImplementation(async () =>
    jsonRes({
      dir: DIRS[0],
      path: '',
      entries: [
        {
          id: 11,
          name: 'sub1',
          rel_path: 'sub1',
          parent_rel: '',
          is_dir: true,
          size: 0,
          mtime: 0,
          code: null,
          thumb_rel: null,
          local_path: null,
          video_count: 0,
          collage: []
        }
      ]
    })
  )
})

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('FilmTree', () => {
  it('渲染根目录列表，只含文件夹的懒加载树', () => {
    const w = mount(FilmTree, {
      props: { dirs: DIRS, currentDirId: null, currentPath: '', refreshTick: 0 }
    })
    const rows = w.findAll('.tree-row.root')
    expect(rows.length).toBe(2)
    expect(rows[0].text()).toContain('a')
    expect(rows[1].text()).toContain('b')
    // 未展开时不发请求
    expect(fetchMock).not.toHaveBeenCalled()
  })

  it('展开根节点：请求该级并只显示文件夹', async () => {
    const w = mount(FilmTree, {
      props: { dirs: DIRS, currentDirId: null, currentPath: '', refreshTick: 0 }
    })
    await w.find('.tree-toggle').trigger('click')
    await flushPromises()

    expect(fetchMock).toHaveBeenCalledTimes(1)
    expect(String(fetchMock.mock.calls[0][0])).toContain('dir_id=1')
    const children = w.findAll('.tree-children .tree-row')
    expect(children.length).toBe(1)
    expect(children[0].text()).toContain('sub1')
  })

  it('点击根目录发出 select(dirId, "")，点子级发出 select(dirId, path)', async () => {
    const w = mount(FilmTree, {
      props: { dirs: DIRS, currentDirId: null, currentPath: '', refreshTick: 0 }
    })
    await w.findAll('.tree-row.root')[0].trigger('click')
    expect(w.emitted('select')![0]).toEqual([1, ''])

    await w.find('.tree-toggle').trigger('click')
    await flushPromises()
    await w.find('.tree-children .tree-row').trigger('click')
    expect(w.emitted('select')![1]).toEqual([1, 'sub1'])
  })
})
