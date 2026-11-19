// @vitest-environment happy-dom
import { describe, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'
import { createMemoryHistory, createRouter } from 'vue-router'
import LibraryView from './LibraryView.vue'

function makeRouter() {
  return createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/', component: LibraryView },
      { path: '/capture', component: { template: '<div />' } }
    ]
  })
}

describe('LibraryView', () => {
  it('首页展示影片管理占位：未实现提示 + 跳转番号抓取入口', async () => {
    const router = makeRouter()
    await router.push('/')
    await router.isReady()

    const wrapper = mount(LibraryView, { global: { plugins: [router] } })
    expect(wrapper.find('h1').text()).toBe('影片管理')
    expect(wrapper.find('.placeholder-title').text()).toContain('功能开发中')

    const link = wrapper.find('.placeholder .btn')
    expect(link.attributes('href')).toBe('/capture')
  })
})
