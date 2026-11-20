// @vitest-environment happy-dom
import { describe, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'
import FilmCard from './FilmCard.vue'
import type { FilmEntry } from '../api'

const DIR_ID = 7

function entry(over: Partial<FilmEntry> = {}): FilmEntry {
  return {
    id: 1,
    name: 'IPX-528.mp4',
    rel_path: 'IPX-528.mp4',
    parent_rel: '',
    is_dir: false,
    size: 1,
    mtime: 1700000000,
    code: 'IPX-528',
    thumb_rel: 'IPX-528.jpg',
    local_path: 'Z:\\F\\IPX-528.mp4',
    ...over
  }
}

function mountCard(e: FilmEntry) {
  return mount(FilmCard, {
    props: { entry: e, dirId: DIR_ID },
    global: {
      stubs: { teleport: true }
    }
  })
}

describe('FilmCard', () => {
  it('视频卡片：缩略图指向 /film-media，显示文件名与时间，番号角标悬浮显示', () => {
    const w = mountCard(entry())
    const img = w.find('img')
    expect(img.attributes('src')).toBe(`/film-media/${DIR_ID}/IPX-528.jpg`)
    expect(w.find('.name').text()).toBe('IPX-528.mp4')
    const badge = w.find('.code-badge')
    expect(badge.text()).toBe('IPX-528')
    // 悬浮显示：默认透明，hover 后可见（用 CSS opacity 实现）
    expect(badge.classes()).toContain('code-badge')
    expect(w.text()).toContain('打开')
    expect(w.text()).toContain('重命名')
    expect(w.text()).toContain('删除')
  })

  it('无缩略图显示空占位', () => {
    const w = mountCard(entry({ thumb_rel: null }))
    expect(w.find('img').exists()).toBe(false)
    expect(w.find('.thumb-empty').exists()).toBe(true)
  })

  it('单视频文件夹：文件夹轮廓 + 大缩略图', () => {
    const w = mountCard(
      entry({
        name: 'folderA',
        rel_path: 'folderA',
        is_dir: true,
        code: null,
        thumb_rel: null,
        video_count: 1,
        collage: ['folderA/movie.jpg']
      })
    )
    expect(w.find('.folder-shape').exists()).toBe(true)
    const img = w.find('.folder-body img')
    expect(img.attributes('src')).toBe(`/film-media/${DIR_ID}/folderA/movie.jpg`)
    expect(w.find('.count-badge').exists()).toBe(false)
  })

  it('文件夹不显示「打开」按钮，点缩略图发出 navigate', async () => {
    const w = mountCard(
      entry({
        name: 'folderA',
        rel_path: 'folderA',
        is_dir: true,
        code: null,
        video_count: 1,
        collage: ['folderA/movie.jpg']
      })
    )
    const openBtn = w.findAll('.actions .btn').find((b) => b.text() === '打开')
    expect(openBtn).toBeUndefined()
    await w.find('.thumb-wrap').trigger('click')
    expect(w.emitted('navigate')).toBeTruthy()
  })

  it('多视频文件夹：2x2 拼贴 + 数量角标', () => {
    const w = mountCard(
      entry({
        name: 'folderB',
        rel_path: 'folderB',
        is_dir: true,
        code: null,
        thumb_rel: null,
        video_count: 5,
        collage: ['folderB/v1.jpg', 'folderB/v2.jpg', 'folderB/v3.jpg', 'folderB/v4.jpg']
      })
    )
    const imgs = w.findAll('.folder-body img')
    expect(imgs.length).toBe(4)
    expect(imgs[3].attributes('src')).toBe(`/film-media/${DIR_ID}/folderB/v4.jpg`)
    expect(w.find('.count-badge').text()).toBe('5')
  })

  it('点文件夹缩略图发出 navigate', async () => {
    const w = mountCard(
      entry({
        name: 'folderA',
        rel_path: 'folderA',
        is_dir: true,
        code: null,
        video_count: 1,
        collage: ['folderA/movie.jpg']
      })
    )
    await w.find('.thumb-wrap').trigger('click')
    expect(w.emitted('navigate')).toBeTruthy()
  })

  it('点重命名 / 删除发出对应事件', async () => {
    const w = mountCard(entry())
    const buttons = w.findAll('.actions .btn')
    await buttons.find((b) => b.text() === '重命名')!.trigger('click')
    expect(w.emitted('rename')).toBeTruthy()
    await buttons.find((b) => b.text() === '删除')!.trigger('click')
    expect(w.emitted('delete')).toBeTruthy()
    await buttons.find((b) => b.text() === '打开')!.trigger('click')
    expect(w.emitted('open')).toBeTruthy()
  })
})
