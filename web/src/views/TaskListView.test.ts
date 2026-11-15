// @vitest-environment happy-dom
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { createMemoryHistory, createRouter } from 'vue-router'
import TaskListView from './TaskListView.vue'
import type { Task } from '../api'

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
    expect(wrapper.find('.count').text()).toBe('10')
    expect(wrapper.find('.sub').text()).toContain('4 / 10')
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
