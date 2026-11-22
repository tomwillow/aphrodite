// @vitest-environment happy-dom
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { createMemoryHistory, createRouter } from 'vue-router'
import TaskListView from './TaskListView.vue'
import type { FilmItemStatus, Task } from '../api'

function task(id: number, status: Task['status'], total = 3, finished = 1): Task {
  return {
    id,
    raw_text: '',
    status,
    total,
    finished,
    error: null,
    rev: 1,
    created_at: Date.now() / 1000,
    updated_at: Date.now() / 1000
  }
}

function filmTask(id: number, status: Task['status'], summary: Partial<Record<FilmItemStatus, number>>): Task {
  return {
    ...task(id, status),
    kind: 'organize',
    raw_text: '整理番号目录 · 全库',
    summary: { pending: 0, done: 0, skipped: 0, failed: 0, ...summary }
  }
}

const fetchMock = vi.fn()

function makeRouter() {
  return createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/', component: { template: '<div />' } },
      { path: '/tasks', component: TaskListView },
      { path: '/tasks/:id', name: 'task', component: { template: '<div />' } }
    ]
  })
}

async function mountList(tasks: Task[]) {
  fetchMock.mockResolvedValueOnce(new Response(JSON.stringify({ tasks }), { status: 200 }))
  const router = makeRouter()
  await router.push('/tasks')
  const wrapper = mount(TaskListView, { global: { plugins: [router] } })
  await flushPromises()
  return { wrapper, router }
}

beforeEach(() => {
  fetchMock.mockReset()
  vi.stubGlobal('fetch', fetchMock)
  vi.stubGlobal('confirm', () => true)
})

describe('TaskListView', () => {
  it('渲染任务列表与进度', async () => {
    const { wrapper } = await mountList([task(1, 'running', 10, 4)])
    expect(wrapper.findAll('.row')).toHaveLength(1)
    expect(wrapper.find('.task-label').text()).toBe('') // 番号抓取任务无标题
    expect(wrapper.find('.sub').text()).toContain('4 / 10')
  })

  it('影片任务显示类型徽标、标题与跳过/失败摘要', async () => {
    const { wrapper } = await mountList([
      filmTask(2, 'running', { done: 4, skipped: 2, failed: 1 })
    ])
    expect(wrapper.find('.kind-badge').text()).toBe('整理目录')
    expect(wrapper.find('.task-label').text()).toContain('整理番号目录')
    expect(wrapper.find('.sub').text()).toContain('跳过 2')
    expect(wrapper.find('.sub').text()).toContain('失败 1')
    expect(wrapper.find('.del.retry').exists()).toBe(false) // 执行中无重试
  })

  it('已完成且含失败条目的影片任务显示重试按钮', async () => {
    const { wrapper } = await mountList([
      filmTask(3, 'completed', { done: 4, failed: 2 })
    ])
    const retry = wrapper.find('.del.retry')
    expect(retry.exists()).toBe(true)

    fetchMock
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ ok: true, task_id: 99 }), { status: 200 })
      )
      .mockResolvedValueOnce(new Response(JSON.stringify({ tasks: [] }), { status: 200 }))
    await retry.trigger('click')
    await flushPromises()

    const [, init] = fetchMock.mock.calls[1]
    expect(fetchMock.mock.calls[1][0]).toBe('/api/tasks/3/retry-failed')
    expect(init.method).toBe('POST')
    expect(wrapper.text()).toContain('#99')
  })

  it('执行中的影片任务显示取消按钮', async () => {
    const { wrapper } = await mountList([filmTask(4, 'running', { done: 1 })])
    const cancel = wrapper.find('.col-actions .del:not(.retry)')
    expect(cancel.exists()).toBe(true)

    fetchMock
      .mockResolvedValueOnce(new Response(JSON.stringify({ ok: true }), { status: 200 }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ tasks: [] }), { status: 200 }))
    await cancel.trigger('click')
    await flushPromises()

    expect(fetchMock.mock.calls[1][0]).toBe('/api/tasks/4/cancel')
  })

  it('点击删除：确认后调用 DELETE 并移除该行', async () => {
    const { wrapper } = await mountList([task(7, 'running')])
    fetchMock.mockResolvedValueOnce(new Response(JSON.stringify({ ok: true }), { status: 200 }))

    await wrapper.find('.del').trigger('click')
    await flushPromises()

    const [url, init] = fetchMock.mock.calls[1]
    expect(url).toBe('/api/tasks/7')
    expect(init.method).toBe('DELETE')
    expect(wrapper.findAll('.row')).toHaveLength(0)
  })

  it('取消确认时不删除', async () => {
    vi.stubGlobal('confirm', () => false)
    const { wrapper } = await mountList([task(9, 'completed')])

    await wrapper.find('.del').trigger('click')
    await flushPromises()

    expect(fetchMock).toHaveBeenCalledTimes(1) // 只有列表加载
    expect(wrapper.findAll('.row')).toHaveLength(1)
  })
})
