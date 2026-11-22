// @vitest-environment happy-dom
import { describe, expect, it } from 'vitest'
import { nextTick } from 'vue'
import { mount } from '@vue/test-utils'
import Lightbox from './Lightbox.vue'
import type { LightboxImage } from './Lightbox.vue'

const IMAGES: LightboxImage[] = [
  { src: '/m/1.jpg', caption: 'a.jpg' },
  { src: '/m/2.jpg', caption: 'b.jpg' },
  { src: '/m/3.jpg', caption: 'c.jpg' }
]

function mountBox(start = 0) {
  return mount(Lightbox, {
    props: { images: IMAGES, start },
    global: { stubs: { teleport: true, transition: true } }
  })
}

function currentSrc(w: ReturnType<typeof mountBox>) {
  return w.find('.viewport img').attributes('src')
}

describe('Lightbox', () => {
  it('从 start 下标开始渲染，HUD 显示文件名与计数', () => {
    const w = mountBox(1)
    expect(currentSrc(w)).toBe('/m/2.jpg')
    expect(w.find('.caption').text()).toBe('b.jpg')
    expect(w.find('.counter').text()).toBe('2 / 3')
    expect(w.text()).toContain('Esc 关闭')
  })

  it('左右箭头翻页，翻页后回到适配模式', async () => {
    const w = mountBox(0)
    await w.find('.nav.next').trigger('click')
    expect(currentSrc(w)).toBe('/m/2.jpg')
    await w.find('.nav.prev').trigger('click')
    expect(currentSrc(w)).toBe('/m/1.jpg')
  })

  it('滚轮向下翻下一张、向上翻上一张；一次手势只翻一张', async () => {
    const w = mountBox(0)
    await w.find('.viewport').trigger('wheel', { deltaY: 100 })
    await w.find('.viewport').trigger('wheel', { deltaY: 50 })
    // 260ms 手势锁内第二次滚动被忽略
    expect(currentSrc(w)).toBe('/m/2.jpg')
    // 等手势锁过期后再往上翻
    await new Promise((r) => setTimeout(r, 300))
    await w.find('.viewport').trigger('wheel', { deltaY: -100 })
    expect(currentSrc(w)).toBe('/m/1.jpg')
  })

  it('到头不循环：顶部弹提示并停在当前张', async () => {
    const w = mountBox(0)
    await w.find('.nav.prev').trigger('click')
    expect(currentSrc(w)).toBe('/m/1.jpg')
    expect(w.find('.toast').text()).toBe('已经是第一张')

    const w2 = mountBox(2)
    await w2.find('.nav.next').trigger('click')
    expect(currentSrc(w2)).toBe('/m/3.jpg')
    expect(w2.find('.toast').text()).toBe('已经是最后一张')
  })

  it('键盘 ←/→ 翻页，Esc 关闭', async () => {
    const w = mountBox(0)
    // 组件监听 window keydown；VTU 默认不挂载到 document，须直接在 window 上派发
    window.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowRight' }))
    await nextTick()
    expect(currentSrc(w)).toBe('/m/2.jpg')
    window.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowLeft' }))
    await nextTick()
    expect(currentSrc(w)).toBe('/m/1.jpg')
    window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }))
    await nextTick()
    expect(w.emitted('close')).toBeTruthy()
  })

  it('点击图片切换 1:1 / 适配，点击空白区域关闭', async () => {
    const w = mountBox(0)
    await w.find('.viewport img').trigger('click')
    expect(w.find('.viewport img').classes()).toContain('zoomed')
    expect(w.find('.viewport').classes()).toContain('zoomed')
    await w.find('.viewport img').trigger('click')
    expect(w.find('.viewport img').classes()).not.toContain('zoomed')
    await w.find('.viewport').trigger('click')
    expect(w.emitted('close')).toBeTruthy()
  })
})
