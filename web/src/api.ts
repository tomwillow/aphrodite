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
