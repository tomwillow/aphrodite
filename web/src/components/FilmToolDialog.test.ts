// @vitest-environment happy-dom
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import FilmToolDialog from './FilmToolDialog.vue'
import type { FilmDir } from '../api'

const DIRS: FilmDir[] = [{ id: 1, path: '/data/films/movies', label: 'movies', valid: 1 }]

const fetchMock = vi.fn()

function mountDialog(props: Record<string, unknown> = {}) {
  return mount(FilmToolDialog, {
    props: {
      open: true,
      tool: 'organize',
      dirs: DIRS,
      currentDirId: 1,
      currentPath: '',
      ...props
    }
  })
}

beforeEach(() => {
  fetchMock.mockReset()
  vi.stubGlobal('fetch', fetchMock)
})

describe('FilmToolDialog', () => {
  it('默认全库启动，POST 到对应端点并发出 started', async () => {
    const w = mountDialog()
    fetchMock.mockResolvedValueOnce(
      new Response(JSON.stringify({ ok: true, task_id: 5 }), { status: 200 })
    )
    await w.findAll('button').find((b) => b.text() === '启动任务')!.trigger('click')
    await flushPromises()

    const [url, init] = fetchMock.mock.calls[0]
    expect(url).toBe('/api/films/tools/organize')
    expect(init.method).toBe('POST')
    expect(JSON.parse(init.body)).toEqual({
      scope: 'all',
      dir_id: null,
      path: '',
      recursive: true
    })
    expect(w.emitted('started')![0]).toEqual([5, 'organize'])
    expect(w.emitted('close')).toBeTruthy()
  })

  it('补缩略图 POST 到 thumbs 端点', async () => {
    const w = mountDialog({ tool: 'thumb' })
    fetchMock.mockResolvedValueOnce(
      new Response(JSON.stringify({ ok: true, task_id: 6 }), { status: 200 })
    )
    await w.findAll('button').find((b) => b.text() === '启动任务')!.trigger('click')
    await flushPromises()
    expect(fetchMock.mock.calls[0][0]).toBe('/api/films/tools/thumbs')
  })

  it('选当前目录时携带 dirId 与当前路径，取消勾选遍历则 recursive=false', async () => {
    const w = mountDialog({ currentPath: 'sub1/deep' })
    await w.find('input[type="radio"][value="current"]').setValue()
    await w.find('input[type="checkbox"]').setValue(false)
    fetchMock.mockResolvedValueOnce(
      new Response(JSON.stringify({ ok: true, task_id: 7 }), { status: 200 })
    )
    await w.findAll('button').find((b) => b.text() === '启动任务')!.trigger('click')
    await flushPromises()
    expect(JSON.parse(fetchMock.mock.calls[0][1].body)).toEqual({
      scope: 'current',
      dir_id: 1,
      path: 'sub1/deep',
      recursive: false
    })
    // 「当前目录」选项要显示实际路径而不是 id
    expect(w.text()).toContain('sub1/deep')
  })

  it('后端 409 时显示错误文案', async () => {
    const w = mountDialog()
    fetchMock.mockResolvedValueOnce(
      new Response(JSON.stringify({ detail: '已有同类任务进行中，请等它结束或取消后再试' }), {
        status: 409
      })
    )
    await w.findAll('button').find((b) => b.text() === '启动任务')!.trigger('click')
    await flushPromises()
    expect(w.find('.err').text()).toContain('已有同类任务进行中')
    expect(w.emitted('close')).toBeFalsy()
  })

  it('关闭按钮发出 close', async () => {
    const w = mountDialog()
    await w.findAll('button').find((b) => b.text() === '取消')!.trigger('click')
    expect(w.emitted('close')).toBeTruthy()
  })
})
