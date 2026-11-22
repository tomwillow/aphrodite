// @vitest-environment happy-dom
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { createMemoryHistory, createRouter } from 'vue-router'
import TaskDetailView from './TaskDetailView.vue'
import type { TaskSnapshot } from '../api'

const ED2K = 'ed2k://|file|@IPX-528_restored.mp4|10174562190|ABC|/'

function snapshot(overrides: Partial<TaskSnapshot['task']> = {}): TaskSnapshot {
  return {
    task: {
      id: 1,
      raw_text: 'IPX-528',
      status: 'completed',
      total: 1,
      finished: 1,
      error: '1 张图片下载失败',
      rev: 10,
      created_at: 0,
      updated_at: 0,
      ...overrides
    },
    items: [
      {
        id: 1,
        task_id: 1,
        seq: 0,
        code: 'IPX-528',
        link: ED2K,
        status: 'done',
        detail_url: null,
        title: '标题',
        error: null,
        cached: 1,
        rev: 5
      }
    ],
    images: [
      {
        id: 1,
        task_id: 1,
        item_id: 1,
        seq: 0,
        kind: 'cover',
        url: 'u',
        path: 'library/IPX-528/cover.jpg',
        status: 'done',
        attempts: 1,
        error: null,
        rev: 6
      },
      {
        id: 2,
        task_id: 1,
        item_id: 1,
        seq: 1,
        kind: 'sample',
        url: 'u2',
        path: null,
        status: 'failed',
        attempts: 5,
        error: 'boom',
        rev: 7
      }
    ]
  }
}

const fetchMock = vi.fn()

class FakeEventSource {
  addEventListener() {}
  close() {}
  onerror: (() => void) | null = null
}

function makeRouter() {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/', component: { template: '<div />' } },
      { path: '/tasks', component: { template: '<div />' } },
      { path: '/tasks/:id', name: 'task', component: TaskDetailView }
    ]
  })
  return router
}

async function mountDetail(snap: TaskSnapshot) {
  fetchMock.mockResolvedValueOnce(new Response(JSON.stringify(snap), { status: 200 }))
  const router = makeRouter()
  await router.push('/tasks/1')
  const wrapper = mount(TaskDetailView, {
    props: { id: '1' },
    global: { plugins: [router] }
  })
  await flushPromises()
  return wrapper
}

beforeEach(() => {
  fetchMock.mockReset()
  vi.stubGlobal('fetch', fetchMock)
  vi.stubGlobal('EventSource', FakeEventSource)
  vi.stubGlobal('confirm', vi.fn(() => true))
})

describe('TaskDetailView', () => {
  it('失败图片显示重试次数，并出现重试按钮', async () => {
    const wrapper = await mountDetail(snapshot())
    expect(wrapper.find('.ph.failed').text()).toContain('5 次')
    expect(wrapper.find('button.retry').text()).toContain('重试失败项（1）')
  })

  it('图库复用的番号显示标记', async () => {
    const wrapper = await mountDetail(snapshot())
    expect(wrapper.find('.tag-cached').exists()).toBe(true)
  })

  it('点击重试调用接口并刷新', async () => {
    const wrapper = await mountDetail(snapshot())
    fetchMock.mockResolvedValueOnce(
      new Response(JSON.stringify({ reset: 1, started: true }), { status: 200 })
    )
    fetchMock.mockResolvedValueOnce(
      new Response(JSON.stringify(snapshot({ status: 'pending' })), { status: 200 })
    )

    await wrapper.find('button.retry').trigger('click')
    await flushPromises()

    const retryCall = fetchMock.mock.calls.find((c) => String(c[0]) === '/api/tasks/1/retry')
    expect(retryCall).toBeTruthy()
    expect(String(fetchMock.mock.calls.at(-1)?.[0])).toBe('/api/tasks/1')
    expect(wrapper.find('.retry-hint').text()).toContain('重新获得 5 次')
  })

  it('没有失败项时不显示重试按钮', async () => {
    const snap = snapshot({ error: null })
    snap.images = snap.images.filter((i) => i.status !== 'failed')
    const wrapper = await mountDetail(snap)
    expect(wrapper.find('button.retry').exists()).toBe(false)
  })

  it('图片下方显示下载链接，并提供一键复制', async () => {
    const writeText = vi.fn(() => Promise.resolve())
    Object.defineProperty(navigator, 'clipboard', {
      value: { writeText },
      configurable: true
    })
    const wrapper = await mountDetail(snapshot())

    expect(wrapper.find('.link-row .link-text').text()).toBe(ED2K)
    await wrapper.find('.link-row button.copy').trigger('click')
    await flushPromises()

    expect(writeText).toHaveBeenCalledWith(ED2K)
    expect(wrapper.find('.link-row button.copy').text()).toBe('已复制')
  })

  it('没有链接的条目不显示链接行', async () => {
    const snap = snapshot()
    snap.items[0].link = null
    const wrapper = await mountDetail(snap)
    expect(wrapper.find('.link-row').exists()).toBe(false)
  })

  it('删除条目调用接口并从页面移除', async () => {
    const wrapper = await mountDetail(snapshot())
    fetchMock.mockResolvedValueOnce(
      new Response(JSON.stringify({ ok: true, images: 2, total: 0 }), { status: 200 })
    )

    await wrapper.find('button.del-item').trigger('click')
    await flushPromises()

    const call = fetchMock.mock.calls.find((c) => String(c[0]) === '/api/tasks/1/items/1')
    expect(call).toBeTruthy()
    expect(call?.[1]?.method).toBe('DELETE')
    expect(wrapper.findAll('.code-row').length).toBe(0)
    expect(wrapper.find('.detail h1').text()).toContain('0 个番号')
  })

  it('影片任务：点击条目跳转影片管理（完成→目标目录，失败→原目录）', async () => {
    const snap = snapshot({ kind: 'organize', raw_text: '整理番号目录 · 全库' })
    snap.items = []
    snap.images = []
    snap.task.film_items = [
      {
        id: 1,
        task_id: 1,
        dir_id: 3,
        rel_path: 'src/ABC-123.mp4',
        name: 'ABC-123.mp4',
        target: 'src/[ABC-123][葵つかさ][タイトル]',
        status: 'done',
        error: null
      },
      {
        id: 2,
        task_id: 1,
        dir_id: 3,
        rel_path: 'other/X.mp4',
        name: 'X.mp4',
        target: '',
        status: 'failed',
        error: '站内未找到该番号'
      }
    ]
    const router = makeRouter()
    await router.push('/tasks/1')
    fetchMock.mockResolvedValueOnce(new Response(JSON.stringify(snap), { status: 200 }))
    const wrapper = mount(TaskDetailView, {
      props: { id: '1' },
      global: { plugins: [router] }
    })
    await flushPromises()

    const rows = wrapper.findAll('.film-row')
    expect(rows.length).toBe(2)
    await rows[0].trigger('click')
    await flushPromises()
    expect(router.currentRoute.value.path).toBe('/')
    expect(router.currentRoute.value.query).toEqual({
      dir: '3',
      path: 'src/[ABC-123][葵つかさ][タイトル]',
      pick: 'ABC-123.mp4'
    })

    await rows[1].trigger('click')
    await flushPromises()
    expect(router.currentRoute.value.query).toEqual({
      dir: '3',
      path: 'other',
      pick: 'X.mp4'
    })
  })

  it('影片任务已完成但没有任何条目：提示「没有需要处理的条目」而不是「条目枚举中」', async () => {
    const snap = snapshot({
      kind: 'organize',
      raw_text: '整理番号目录 · wandoujia/LTX/OFJE-041-LTX（含子目录）',
      status: 'completed',
      total: 0,
      finished: 0,
      error: null
    })
    snap.items = []
    snap.images = []
    snap.task.film_items = []
    const wrapper = await mountDetail(snap)
    const empty = wrapper.find('.film-items .empty')
    expect(empty.text()).toContain('没有需要处理的条目')
    expect(empty.text()).not.toContain('条目枚举中')
  })
})
