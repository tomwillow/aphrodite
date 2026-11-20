// @vitest-environment happy-dom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import LibraryView from './LibraryView.vue'
import type { FilmEntriesResp, FilmDir, FilmEntry } from '../api'

const DIR_A: FilmDir = { id: 1, path: '/data/films/a', label: 'a', valid: 1 }

function videoEntry(over: Partial<FilmEntry> = {}): FilmEntry {
  return {
    id: 10,
    name: 'IPX-528.mp4',
    rel_path: 'IPX-528.mp4',
    parent_rel: '',
    is_dir: false,
    size: 1,
    mtime: 1700000000,
    code: 'IPX-528',
    thumb_rel: 'IPX-528.jpg',
    local_path: 'Z:\\Films\\IPX-528.mp4',
    ...over
  }
}

function entriesResp(entries: FilmEntry[]): FilmEntriesResp {
  return { dir: DIR_A, path: '', entries }
}

const fetchMock = vi.fn()

function jsonRes(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status })
}

beforeEach(() => {
  fetchMock.mockReset()
  vi.stubGlobal('fetch', fetchMock)
  fetchMock.mockImplementation(async (url: string, init?: RequestInit) => {
    if (url === '/api/films/dirs') {
      return jsonRes({ dirs: [DIR_A], mapped: true })
    }
    if (url.startsWith('/api/films/entries')) {
      return jsonRes(entriesResp([videoEntry()]))
    }
    if (url === '/api/films/delete') {
      return jsonRes({ ok: true })
    }
    return jsonRes({ detail: 'Not Found' }, 404)
  })
})

afterEach(() => {
  vi.unstubAllGlobals()
})

async function mountView() {
  const wrapper = mount(LibraryView)
  await flushPromises()
  return wrapper
}

describe('LibraryView', () => {
  it('挂载后自动选中第一个影片目录并渲染视频卡片', async () => {
    const wrapper = await mountView()
    expect(wrapper.text()).toContain('影片管理')
    expect(wrapper.find('.grid').exists()).toBe(true)
    expect(wrapper.findAll('.film-card').length).toBe(1)
    expect(wrapper.text()).toContain('IPX-528.mp4')
  })

  it('右上角下拉切换视图：按分类显示开发中占位', async () => {
    const wrapper = await mountView()
    await wrapper.find('.view-switch button').trigger('click')
    const items = wrapper.findAll('.switch-item')
    expect(items.length).toBe(2)
    await items[1].trigger('click')
    expect(wrapper.text()).toContain('分类视图开发中')
    expect(wrapper.find('.film-tree').exists()).toBe(false)
  })

  it('删除需要确认：弹窗显示文件名与警告，确认后调用删除接口', async () => {
    const wrapper = await mountView()
    await wrapper.find('.film-card .actions .danger').trigger('click')

    const modal = wrapper.find('.modal')
    expect(modal.exists()).toBe(true)
    expect(modal.text()).toContain('IPX-528.mp4')
    expect(modal.text()).toContain('不可恢复')

    await wrapper.find('.danger-solid').trigger('click')
    await flushPromises()

    const call = fetchMock.mock.calls.find(
      ([u, init]) => u === '/api/films/delete' && init?.method === 'POST'
    )
    expect(call).toBeDefined()
    expect(JSON.parse((call![1] as RequestInit).body as string)).toEqual({ entry_id: 10 })
    // 确认后弹窗关闭并重新拉取列表
    expect(wrapper.find('.modal').exists()).toBe(false)
  })

  it('没有本机路径映射时点「打开」显示错误而不是触发协议', async () => {
    fetchMock.mockImplementation(async (url: string) => {
      if (url === '/api/films/dirs') return jsonRes({ dirs: [DIR_A], mapped: false })
      if (url.startsWith('/api/films/entries')) {
        return jsonRes(entriesResp([videoEntry({ local_path: null })]))
      }
      return jsonRes({ detail: 'Not Found' }, 404)
    })
    const wrapper = await mountView()
    await wrapper.find('.film-card .actions .btn').trigger('click')
    expect(wrapper.find('.err').text()).toContain('APHRODITE_FILM_PATH_MAP')
  })
})
