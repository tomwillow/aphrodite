// @vitest-environment happy-dom
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { createMemoryHistory, createRouter } from 'vue-router'
import SubmitView from './SubmitView.vue'
import type { TaskSnapshot } from '../api'

const ED2K = 'ed2k://|file|@IPX-528_restored.mp4|10174562190|ABC|/'
const MAGNET = 'magnet:?xt=urn:btih:F91AF5AFD93EBE9D9FDD390AB00E3C1334C8AA69'

function snapshot(id: number, count: number): TaskSnapshot {
  return {
    task: {
      id,
      raw_text: 'x',
      status: 'pending',
      total: count,
      finished: 0,
      error: null,
      rev: 1,
      created_at: 0,
      updated_at: 0
    },
    items: [],
    images: []
  }
}

function makeRouter() {
  return createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/', component: SubmitView },
      { path: '/tasks/:id', name: 'task', component: { template: '<div />' } }
    ]
  })
}

function mountView(router = makeRouter()) {
  const wrapper = mount(SubmitView, { global: { plugins: [router] } })
  return { wrapper, router }
}

const fetchMock = vi.fn()

beforeEach(() => {
  fetchMock.mockReset()
  vi.stubGlobal('fetch', fetchMock)
})

describe('SubmitView', () => {
  it('初始状态：计数为 0，提交按钮禁用', () => {
    const { wrapper } = mountView()
    expect(wrapper.find('.num').text()).toBe('0')
    expect(wrapper.find('button.btn-primary').attributes('disabled')).toBeDefined()
    expect(fetchMock).not.toHaveBeenCalled()
  })

  it('正常条目（番号 + 链接）只显示数量，不逐条罗列', async () => {
    fetchMock.mockResolvedValue(new Response(JSON.stringify(snapshot(7, 2)), { status: 200 }))
    const { wrapper, router } = mountView()

    await wrapper
      .find('textarea')
      .setValue([ED2K, ED2K.replace('IPX-528', 'AVOP-201')].join('\n'))

    expect(wrapper.find('.num').text()).toBe('2')
    // 没有分类提示，说明全部识别正常
    expect(wrapper.find('.problems').exists()).toBe(false)
    // 也不再逐个渲染番号 chip
    expect(wrapper.findAll('.chip').length).toBe(0)

    await wrapper.find('button.btn-primary').trigger('click')
    await flushPromises()

    expect(fetchMock).toHaveBeenCalledTimes(1)
    const [url, init] = fetchMock.mock.calls[0]
    expect(url).toBe('/api/tasks')
    expect(init.method).toBe('POST')
    expect(JSON.parse(init.body).text).toContain('IPX-528')
    expect(router.currentRoute.value.fullPath).toBe('/tasks/7')
  })

  it('分类展示：缺链接 / 只有链接 / 未识别', async () => {
    const { wrapper } = mountView()
    await wrapper
      .find('textarea')
      .setValue([ED2K, 'IPX-529 田中レモン', MAGNET, '这一行什么都没识别出来'].join('\n'))

    // 只有 1 个「正常」番号
    expect(wrapper.find('.num').text()).toBe('1')
    const cards = wrapper.findAll('.problem-card')
    expect(cards.length).toBe(3)
    expect(cards[0].text()).toContain('缺少下载链接')
    expect(cards[0].text()).toContain('IPX-529')
    expect(cards[1].text()).toContain('只有链接，没有番号')
    expect(cards[1].text()).toContain('magnet:?xt=')
    expect(cards[2].text()).toContain('未识别出番号')
    expect(cards[2].text()).toContain('这一行什么都没识别出来')
  })

  it('只有链接没有番号的文本无法提交', async () => {
    const { wrapper } = mountView()
    await wrapper.find('textarea').setValue(MAGNET)
    expect(wrapper.find('.num').text()).toBe('0')
    expect(wrapper.find('button.btn-primary').attributes('disabled')).toBeDefined()
  })

  it('回归：649 个番号（test.txt 规模）也能正常提交', async () => {
    fetchMock.mockResolvedValue(new Response(JSON.stringify(snapshot(8, 649)), { status: 200 }))
    const { wrapper, router } = mountView()

    const big = Array.from(
      { length: 649 },
      (_, i) => `ed2k://|file|@AB-${String(i).padStart(3, '0')}.mp4|1|HASH|/`
    ).join('\n')
    await wrapper.find('textarea').setValue(big)

    expect(wrapper.find('.num').text()).toBe('649')

    await wrapper.find('button.btn-primary').trigger('click')
    await flushPromises()

    expect(fetchMock).toHaveBeenCalledTimes(1)
    expect(router.currentRoute.value.fullPath).toBe('/tasks/8')
  })

  it('服务端返回 400 时：显示错误且不跳转', async () => {
    fetchMock.mockResolvedValue(
      new Response(JSON.stringify({ detail: '单个任务最多 2000 个番号，请分批提交' }), { status: 400 })
    )
    const { wrapper, router } = mountView()

    await wrapper.find('textarea').setValue(ED2K)
    await wrapper.find('button.btn-primary').trigger('click')
    await flushPromises()

    expect(wrapper.find('.err').text()).toContain('2000')
    expect(router.currentRoute.value.fullPath).toBe('/')
  })
})
