// @vitest-environment happy-dom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { createMemoryHistory, createRouter } from 'vue-router'
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

function folderEntry(over: Partial<FilmEntry> = {}): FilmEntry {
  return videoEntry({
    id: 20,
    name: 'folderA',
    rel_path: 'folderA',
    is_dir: true,
    code: null,
    thumb_rel: null,
    local_path: null,
    video_count: 1,
    collage: ['folderA/movie.jpg'],
    ...over
  })
}

function entriesResp(entries: FilmEntry[], scanning = false): FilmEntriesResp {
  return { dir: DIR_A, path: '', entries, scanning }
}

class FakeEventSource {
  static instances: FakeEventSource[] = []
  listeners: Record<string, Array<(ev: { data: string }) => void>> = {}
  closed = false
  constructor(public url: string) {
    FakeEventSource.instances.push(this)
  }
  addEventListener(name: string, cb: (ev: { data: string }) => void) {
    ;(this.listeners[name] ??= []).push(cb)
  }
  close() {
    this.closed = true
  }
  emit(name: string, data: unknown) {
    for (const cb of this.listeners[name] ?? []) cb({ data: JSON.stringify(data) })
  }
}

const fetchMock = vi.fn()

function jsonRes(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status })
}

beforeEach(() => {
  FakeEventSource.instances = []
  fetchMock.mockReset()
  vi.stubGlobal('fetch', fetchMock)
  vi.stubGlobal('EventSource', FakeEventSource)
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

async function mountView(query: Record<string, string> = {}) {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [{ path: '/', component: LibraryView }]
  })
  await router.push({ path: '/', query })
  await router.isReady()
  const wrapper = mount(LibraryView, { global: { plugins: [router] } })
  await flushPromises()
  return wrapper
}

describe('LibraryView', () => {
  it('挂载后自动选中第一个影片目录并渲染视频卡片，建立 SSE 连接', async () => {
    const wrapper = await mountView()
    expect(wrapper.text()).toContain('影片管理')
    expect(wrapper.find('.grid').exists()).toBe(true)
    expect(wrapper.findAll('.film-card').length).toBe(1)
    expect(wrapper.text()).toContain('IPX-528.mp4')
    expect(FakeEventSource.instances.length).toBe(1)
    expect(FakeEventSource.instances[0].url).toBe('/api/films/stream')
  })

  it('深链 ?dir&path&pick：选中指定目录并高亮 pick 条目', async () => {
    const wrapper = await mountView({ dir: '1', path: '', pick: 'IPX-528.mp4' })
    expect(wrapper.find('.grid').exists()).toBe(true)
    const picked = wrapper.find('.picked-flash')
    expect(picked.exists()).toBe(true)
    expect(picked.text()).toContain('IPX-528.mp4')
  })

  it('快照阶段（scanning=true）显示同步提示，scan 事件到达后替换为完整数据', async () => {
    fetchMock.mockImplementation(async (url: string) => {
      if (url === '/api/films/dirs') return jsonRes({ dirs: [DIR_A], mapped: true })
      if (url.startsWith('/api/films/entries')) {
        return jsonRes(entriesResp([videoEntry({ thumb_rel: null })], true))
      }
      return jsonRes({ detail: 'Not Found' }, 404)
    })
    const wrapper = await mountView()
    expect(wrapper.text()).toContain('同步中')

    const es = FakeEventSource.instances[0]
    es.emit('scan', {
      dir_id: 1,
      parent_rel: '',
      entries: [videoEntry(), folderEntry({ collage: [] })]
    })
    await flushPromises()
    expect(wrapper.findAll('.film-card').length).toBe(2)
    expect(wrapper.text()).not.toContain('同步中')
  })

  it('scan 事件指向其他级时忽略', async () => {
    const wrapper = await mountView()
    const before = wrapper.findAll('.film-card').length
    FakeEventSource.instances[0].emit('scan', {
      dir_id: 1,
      parent_rel: 'other/path',
      entries: [videoEntry({ id: 99, name: '别处的.mp4' })]
    })
    await flushPromises()
    expect(wrapper.findAll('.film-card').length).toBe(before)
    expect(wrapper.text()).not.toContain('别处的.mp4')
  })

  it('scan 事件带 error 时显示错误信息', async () => {
    const wrapper = await mountView()
    FakeEventSource.instances[0].emit('scan', {
      dir_id: 1,
      parent_rel: '',
      entries: null,
      error: '影片目录不可访问'
    })
    await flushPromises()
    expect(wrapper.find('.err').text()).toContain('影片目录不可访问')
  })

  it('右上角刷新按钮带 force=1 强制真实读盘', async () => {
    const wrapper = await mountView()
    fetchMock.mockClear()
    await wrapper.findAll('button').find((b) => b.text().includes('刷新'))!.trigger('click')
    await flushPromises()
    const call = fetchMock.mock.calls.find(([u]) => String(u).startsWith('/api/films/entries'))
    expect(String(call![0])).toContain('force=1')
  })

  it('卸载时关闭 SSE 连接', async () => {
    const wrapper = await mountView()
    const es = FakeEventSource.instances[0]
    wrapper.unmount()
    expect(es.closed).toBe(true)
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
