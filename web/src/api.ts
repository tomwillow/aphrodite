export type TaskStatus = 'pending' | 'running' | 'completed' | 'failed'
export type ItemStatus = 'pending' | 'searching' | 'downloading' | 'done' | 'notfound' | 'failed'
export type ImageStatus = 'pending' | 'downloading' | 'done' | 'failed' | 'skipped'

export interface Task {
  id: number
  raw_text: string
  status: TaskStatus
  total: number
  finished: number
  error: string | null
  rev: number
  created_at: number
  updated_at: number
}

export interface TaskItem {
  id: number
  task_id: number
  seq: number
  code: string
  link: string | null
  status: ItemStatus
  detail_url: string | null
  title: string | null
  error: string | null
  cached: number
  rev: number
}

export interface TaskImage {
  id: number
  task_id: number
  item_id: number
  seq: number
  kind: 'cover' | 'sample'
  url: string | null
  path: string | null
  status: ImageStatus
  attempts: number
  error: string | null
  rev: number
}

export interface TaskSnapshot {
  task: Task
  items: TaskItem[]
  images: TaskImage[]
}

export interface FilmDir {
  id: number
  path: string
  label: string
  valid: number
}

export interface FilmEntry {
  id: number
  name: string
  rel_path: string
  parent_rel: string
  is_dir: boolean
  size: number
  mtime: number
  code: string | null
  thumb_rel: string | null
  local_path: string | null
  /** 仅文件夹：子级视频数与拼贴缩略图（相对根目录的路径） */
  video_count?: number
  collage?: string[]
}

export interface FilmEntriesResp {
  dir: FilmDir
  path: string
  entries: FilmEntry[]
}

export interface FilmHandler {
  bat: string
  ps1: string
  readme: string
}

async function parse<T>(res: Response): Promise<T> {
  if (!res.ok) {
    let detail = res.statusText
    try {
      const body = await res.json()
      if (body?.detail) detail = String(body.detail)
    } catch {
      /* ignore */
    }
    throw new Error(detail)
  }
  return (await res.json()) as T
}

export async function createTask(text: string): Promise<TaskSnapshot> {
  const res = await fetch('/api/tasks', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ text })
  })
  return parse<TaskSnapshot>(res)
}

export async function fetchTasks(): Promise<Task[]> {
  const res = await fetch('/api/tasks')
  const data = await parse<{ tasks: Task[] }>(res)
  return data.tasks
}

export async function fetchTask(id: number | string): Promise<TaskSnapshot> {
  const res = await fetch(`/api/tasks/${id}`)
  return parse<TaskSnapshot>(res)
}

export async function deleteTask(id: number): Promise<void> {
  const res = await fetch(`/api/tasks/${id}`, { method: 'DELETE' })
  await parse<{ ok: boolean }>(res)
}

export async function deleteTaskItem(
  taskId: number,
  itemId: number
): Promise<{ ok: boolean; images: number; total: number }> {
  const res = await fetch(`/api/tasks/${taskId}/items/${itemId}`, { method: 'DELETE' })
  return parse<{ ok: boolean; images: number; total: number }>(res)
}

export async function retryTask(
  id: number
): Promise<{ reset: number; unfinished: number; started: boolean }> {
  const res = await fetch(`/api/tasks/${id}/retry`, { method: 'POST' })
  return parse<{ reset: number; unfinished: number; started: boolean }>(res)
}

export function mediaUrl(path: string): string {
  return `/media/${path.split('/').map(encodeURIComponent).join('/')}`
}

// ---------------------------------------------------------------- 影片管理

export async function fetchFilmDirs(): Promise<{ dirs: FilmDir[]; mapped: boolean }> {
  const res = await fetch('/api/films/dirs')
  return parse(res)
}

export async function fetchFilmEntries(dirId: number | string, path: string): Promise<FilmEntriesResp> {
  const q = `dir_id=${encodeURIComponent(dirId)}&path=${encodeURIComponent(path)}`
  const res = await fetch(`/api/films/entries?${q}`)
  return parse<FilmEntriesResp>(res)
}

export async function renameFilmEntry(entryId: number, newName: string): Promise<void> {
  const res = await fetch('/api/films/rename', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ entry_id: entryId, new_name: newName })
  })
  await parse<{ ok: boolean }>(res)
}

export async function deleteFilmEntry(entryId: number): Promise<void> {
  const res = await fetch('/api/films/delete', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ entry_id: entryId })
  })
  await parse<{ ok: boolean }>(res)
}

export async function fetchFilmHandler(): Promise<FilmHandler> {
  const res = await fetch('/api/films/handler')
  return parse<FilmHandler>(res)
}

export function filmMediaUrl(dirId: number | string, relPath: string): string {
  return `/film-media/${dirId}/${relPath.split('/').map(encodeURIComponent).join('/')}`
}

/** 本机播放路径 → aphrodite-play:// 协议地址（统一正斜杠，bat 处理器可直接 start） */
export function playUrl(localPath: string): string {
  return `aphrodite-play://${localPath.replace(/\\/g, '/')}`
}
