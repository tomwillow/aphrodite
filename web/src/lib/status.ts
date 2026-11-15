import type { ImageStatus, ItemStatus, TaskStatus } from '../api'

export const TASK_STATUS_TEXT: Record<TaskStatus, string> = {
  pending: '排队中',
  running: '执行中',
  completed: '已完成',
  failed: '异常'
}

export const ITEM_STATUS_TEXT: Record<ItemStatus, string> = {
  pending: '排队中',
  searching: '搜索中',
  downloading: '下载中',
  done: '已完成',
  notfound: '未找到',
  failed: '失败'
}

export const IMAGE_STATUS_TEXT: Record<ImageStatus, string> = {
  pending: '未下载',
  downloading: '下载中',
  done: '',
  failed: '下载失败',
  skipped: '无图'
}

const pad = (n: number) => String(n).padStart(2, '0')

export function fmtTime(ts: number): string {
  const d = new Date(ts * 1000)
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(
    d.getMinutes()
  )}`
}

export function relTime(ts: number): string {
  const diff = Date.now() / 1000 - ts
  if (diff < 60) return '刚刚'
  if (diff < 3600) return `${Math.floor(diff / 60)} 分钟前`
  if (diff < 86400) return `${Math.floor(diff / 3600)} 小时前`
  if (diff < 86400 * 30) return `${Math.floor(diff / 86400)} 天前`
  return fmtTime(ts)
}
