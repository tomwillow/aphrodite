<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import Lightbox from '../components/Lightbox.vue'
import {
  deleteTaskItem,
  fetchTask,
  mediaUrl,
  retryFailedItems,
  retryTask,
  type FilmTaskItem,
  type Task,
  type TaskImage,
  type TaskItem
} from '../api'
import {
  FILM_ITEM_STATUS_TEXT,
  IMAGE_STATUS_TEXT,
  ITEM_STATUS_TEXT,
  TASK_STATUS_TEXT,
  fmtTime
} from '../lib/status'

const props = defineProps<{ id: string }>()
const router = useRouter()

const LEVELS = [
  { key: 'sm', label: '小', h: 96 },
  { key: 'md', label: '中', h: 144 },
  { key: 'lg', label: '大', h: 200 },
  { key: 'xl', label: '特大', h: 280 }
] as const

type LevelKey = (typeof LEVELS)[number]['key']

const task = ref<Task | null>(null)
const items = ref<TaskItem[]>([])
const images = ref<TaskImage[]>([])
const filmItems = ref<FilmTaskItem[]>([])
const loading = ref(true)
const error = ref('')
const finished = ref(false)
const retrying = ref(false)
const retryHint = ref('')
const lightbox = ref<{ src: string; caption: string } | null>(null)
const deletingItem = ref<number | null>(null)
const copyState = ref<{ id: number; ok: boolean } | null>(null)

let rev = 0
let es: EventSource | null = null
let retryTimer: number | undefined
let copyTimer: number | undefined
let pollTimer: number | undefined

const isFilmTask = computed(
  () => task.value?.kind === 'organize' || task.value?.kind === 'thumb'
)

const filmFailedCount = computed(
  () => filmItems.value.filter((i) => i.status === 'failed').length
)

const filmDoneCount = computed(
  () => filmItems.value.filter((i) => i.status === 'done').length
)

const filmSkippedCount = computed(
  () => filmItems.value.filter((i) => i.status === 'skipped').length
)

const size = ref<LevelKey>((localStorage.getItem('aphrodite.shot') as LevelKey) || 'md')
const shotHeight = computed(() => LEVELS.find((l) => l.key === size.value)?.h ?? 144)

function setSize(key: LevelKey) {
  size.value = key
  localStorage.setItem('aphrodite.shot', key)
}

const imagesByItem = computed(() => {
  const map = new Map<number, TaskImage[]>()
  for (const img of images.value) {
    if (img.status === 'skipped') continue
    const arr = map.get(img.item_id)
    if (arr) arr.push(img)
    else map.set(img.item_id, [img])
  }
  for (const arr of map.values()) arr.sort((a, b) => a.seq - b.seq)
  return map
})

const progressPct = computed(() => {
  const t = task.value
  if (!t || !t.total) return 0
  return Math.round((t.finished / t.total) * 100)
})

const isLive = computed(
  () => !!task.value && (task.value.status === 'running' || task.value.status === 'pending')
)

const failedCount = computed(
  () =>
    images.value.filter((i) => i.status === 'failed').length +
    items.value.filter((i) => i.status === 'failed').length
)

const canRetry = computed(
  () => !!task.value && (task.value.status === 'failed' || failedCount.value > 0)
)

function mergeItems(rows: TaskItem[]) {
  const map = new Map(items.value.map((i) => [i.id, i]))
  for (const r of rows) {
    map.set(r.id, { ...(map.get(r.id) as TaskItem), ...r })
    rev = Math.max(rev, r.rev)
  }
  items.value = [...map.values()].sort((a, b) => a.seq - b.seq)
}

function mergeImages(rows: TaskImage[]) {
  const map = new Map(images.value.map((i) => [i.id, i]))
  for (const r of rows) {
    map.set(r.id, { ...(map.get(r.id) as TaskImage), ...r })
    rev = Math.max(rev, r.rev)
  }
  images.value = [...map.values()].sort((a, b) => a.item_id - b.item_id || a.seq - b.seq)
}

function markFinished() {
  finished.value = true
  es?.close()
  es = null
}

async function loadFull() {
  const snap = await fetchTask(props.id)
  task.value = snap.task
  items.value = snap.items
  images.value = [...snap.images].sort((a, b) => a.item_id - b.item_id || a.seq - b.seq)
  filmItems.value = snap.task.film_items ?? []
  rev = Math.max(
    rev,
    snap.task.rev,
    ...snap.items.map((i) => i.rev),
    ...snap.images.map((i) => i.rev)
  )
  if (snap.task.status === 'completed' || snap.task.status === 'failed') markFinished()
  loading.value = false
}

function startPolling() {
  if (finished.value) return
  pollTimer = window.setInterval(async () => {
    if (document.visibilityState !== 'visible') return
    try {
      const snap = await fetchTask(props.id)
      task.value = snap.task
      filmItems.value = snap.task.film_items ?? []
      if (snap.task.status === 'completed' || snap.task.status === 'failed') {
        finished.value = true
        if (pollTimer) window.clearInterval(pollTimer)
        pollTimer = undefined
      }
    } catch {
      /* 忽略，下一轮再试 */
    }
  }, 1800)
}

function connect() {
  if (finished.value) return
  es?.close()
  es = new EventSource(`/api/tasks/${props.id}/stream?rev=${rev}`)

  es.addEventListener('task', (ev) => {
    const row = JSON.parse((ev as MessageEvent).data) as Task
    task.value = { ...(task.value as Task), ...row }
    rev = Math.max(rev, row.rev)
  })
  es.addEventListener('items', (ev) => {
    mergeItems(JSON.parse((ev as MessageEvent).data) as TaskItem[])
  })
  es.addEventListener('images', (ev) => {
    mergeImages(JSON.parse((ev as MessageEvent).data) as TaskImage[])
  })
  es.addEventListener('removed', (ev) => {
    const data = JSON.parse((ev as MessageEvent).data) as { items: number[]; images: number[] }
    if (data.items.length) {
      const gone = new Set(data.items)
      items.value = items.value.filter((i) => !gone.has(i.id))
      images.value = images.value.filter((i) => !gone.has(i.item_id))
    }
    if (data.images.length) {
      const gone = new Set(data.images)
      images.value = images.value.filter((i) => !gone.has(i.id))
    }
  })
  es.addEventListener('done', () => markFinished())
  es.onerror = () => {
    if (finished.value) return
    es?.close()
    es = null
    if (retryTimer) window.clearTimeout(retryTimer)
    retryTimer = window.setTimeout(async () => {
      try {
        await loadFull()
      } catch {
        /* 忽略，下一轮再试 */
      }
      connect()
    }, 1500)
  }
}

onMounted(async () => {
  try {
    await loadFull()
    if (isFilmTask.value) startPolling()
    else connect()
  } catch (e) {
    error.value = e instanceof Error ? e.message : '加载失败'
    loading.value = false
  }
})

onBeforeUnmount(() => {
  finished.value = true
  es?.close()
  es = null
  if (retryTimer) window.clearTimeout(retryTimer)
  if (copyTimer) window.clearTimeout(copyTimer)
  if (pollTimer) window.clearInterval(pollTimer)
})

function openImage(img: TaskImage, item: TaskItem) {
  if (!img.path) return
  lightbox.value = {
    src: mediaUrl(img.path),
    caption: `${item.code} · ${img.kind === 'cover' ? '主图' : `详情 ${img.seq}`}`
  }
}

async function retryFailed() {
  if (!task.value || retrying.value) return
  retrying.value = true
  retryHint.value = ''
  try {
    const res = await retryTask(task.value.id)
    if (!res.reset && !res.unfinished) {
      retryHint.value = '没有需要重试的项目'
      return
    }
    retryHint.value = res.reset
      ? `已重新加入 ${res.reset} 张图片，重新获得 5 次尝试机会`
      : `继续处理 ${res.unfinished} 个未完成的番号`
    finished.value = false
    await loadFull()
    connect()
  } catch (e) {
    retryHint.value = e instanceof Error ? e.message : '重试失败'
  } finally {
    retrying.value = false
  }
}

async function retryFilmFailed() {
  if (!task.value || retrying.value) return
  retrying.value = true
  retryHint.value = ''
  try {
    const newId = await retryFailedItems(task.value.id)
    retryHint.value = `已生成重试任务 #${newId}，仅处理失败条目`
  } catch (e) {
    retryHint.value = e instanceof Error ? e.message : '重试失败'
  } finally {
    retrying.value = false
  }
}

function thumbStyle() {
  return { '--shot-h': `${shotHeight.value}px` }
}

/** 点击影片任务条目 → 跳到影片管理页该文件所在目录并高亮。 */
function jumpToFilm(it: FilmTaskItem) {
  let path: string
  if (it.status === 'done' && it.target) {
    // organize 的 target 是目标目录本身；thumb 的 target 是生成的缩略图文件路径
    path =
      task.value?.kind === 'organize'
        ? it.target
        : it.target.split('/').slice(0, -1).join('/')
  } else {
    // 未完成/失败：文件还在原位，去它的上级目录
    path = it.rel_path.includes('/')
      ? it.rel_path.split('/').slice(0, -1).join('/')
      : ''
  }
  router.push({ path: '/', query: { dir: String(it.dir_id), path, pick: it.name } })
}

async function removeItem(item: TaskItem) {
  if (!task.value || deletingItem.value) return
  const busy = item.status === 'pending' || item.status === 'searching' || item.status === 'downloading'
  const tip = busy
    ? `${item.code} 正在下载，删除会立即停止下载并移除这一行。\n已下载的图片会保留在本地图库中。确定删除？`
    : `删除 ${item.code} 这一行？\n已下载的图片会保留在本地图库中。`
  if (!window.confirm(tip)) return
  deletingItem.value = item.id
  try {
    await deleteTaskItem(task.value.id, item.id)
    items.value = items.value.filter((i) => i.id !== item.id)
    images.value = images.value.filter((i) => i.item_id !== item.id)
    task.value.total = Math.max(0, task.value.total - 1)
    task.value.finished = Math.min(task.value.finished, task.value.total)
  } catch (e) {
    retryHint.value = e instanceof Error ? e.message : '删除失败'
  } finally {
    deletingItem.value = null
  }
}

async function copyLink(item: TaskItem) {
  const link = item.link
  if (!link) return
  let ok = true
  try {
    await navigator.clipboard.writeText(link)
  } catch {
    // 非安全上下文 / 无剪贴板权限时的兜底
    try {
      const ta = document.createElement('textarea')
      ta.value = link
      ta.setAttribute('readonly', '')
      ta.style.position = 'fixed'
      ta.style.left = '-9999px'
      document.body.appendChild(ta)
      ta.select()
      ok = document.execCommand('copy')
      document.body.removeChild(ta)
    } catch {
      ok = false
    }
  }
  copyState.value = { id: item.id, ok }
  if (copyTimer) window.clearTimeout(copyTimer)
  copyTimer = window.setTimeout(() => {
    if (copyState.value?.id === item.id) copyState.value = null
  }, 1800)
}

function isHttpLink(link: string) {
  return /^https?:/i.test(link)
}
</script>

<template>
  <section v-if="loading" class="card empty">加载中…</section>
  <p v-else-if="error" class="card empty">{{ error }}</p>

  <section v-else-if="task" class="detail">
    <header class="page-head">
      <div class="head-text">
        <div class="crumbs muted small">
          <router-link to="/tasks">任务列表</router-link>
          <span class="slash">/</span>
          <span>#{{ task.id }}</span>
        </div>
        <h1>
          <template v-if="isFilmTask">
            {{ task.raw_text }}
            <span class="badge" :class="task.status">
              <span v-if="isLive" class="pulse"></span>{{ TASK_STATUS_TEXT[task.status] }}
            </span>
          </template>
          <template v-else>
            {{ task.total }} 个番号
            <span class="badge" :class="task.status">
              <span v-if="isLive" class="pulse"></span>{{ TASK_STATUS_TEXT[task.status] }}
            </span>
          </template>
        </h1>
        <p class="muted small">
          {{ fmtTime(task.created_at) }} 提交 · {{ task.finished }}/{{ task.total }} 条已处理
          <template v-if="isFilmTask && (filmSkippedCount || filmFailedCount)">
            · 跳过 {{ filmSkippedCount }} · 失败 {{ filmFailedCount }}
          </template>
          <span v-if="task.error"> · {{ task.error }}</span>
        </p>
      </div>

      <div class="head-actions">
        <button
          v-if="isFilmTask && (task.status === 'completed' || task.status === 'failed') && filmFailedCount"
          class="btn btn-sm retry"
          :disabled="retrying"
          @click="retryFilmFailed"
        >
          {{ retrying ? '重试中…' : `重试失败条目（${filmFailedCount}）` }}
        </button>
        <button v-if="!isFilmTask && canRetry" class="btn btn-sm retry" :disabled="retrying" @click="retryFailed">
          {{
            retrying
              ? '重试中…'
              : failedCount
                ? `重试失败项（${failedCount}）`
                : '继续任务'
          }}
        </button>
        <span class="muted small" v-if="!isFilmTask">图片大小</span>
        <div class="segmented" v-if="!isFilmTask">
          <button
            v-for="lv in LEVELS"
            :key="lv.key"
            :class="{ active: size === lv.key }"
            @click="setSize(lv.key)"
          >
            {{ lv.label }}
          </button>
        </div>
      </div>
    </header>

    <p v-if="retryHint" class="retry-hint small">{{ retryHint }}</p>

    <div class="progress sticky-progress">
      <div class="bar" :style="{ width: progressPct + '%' }"></div>
    </div>

    <div v-if="isFilmTask" class="film-items">
      <div v-if="!filmItems.length" class="card empty">
        <template v-if="isLive">条目枚举中…（不做预扫，任务启动后动态增加）</template>
        <template v-else-if="task.error">没有需要处理的条目：{{ task.error }}</template>
        <template v-else>没有需要处理的条目（目录里没有可整理/可补图的视频）</template>
      </div>
      <button
        v-for="it in filmItems"
        :key="it.id"
        class="film-row"
        :title="`查看 ${it.name} 在影片管理中的位置`"
        @click="jumpToFilm(it)"
      >
        <span class="film-name" :title="it.rel_path">{{ it.name }}</span>
        <span v-if="it.target" class="film-target muted" :title="it.target">→ {{ it.target }}</span>
        <span class="badge" :class="it.status">{{ FILM_ITEM_STATUS_TEXT[it.status] }}</span>
        <span v-if="it.error" class="item-err">{{ it.error }}</span>
        <span class="film-jump">查看 ↗</span>
      </button>
    </div>

    <template v-else>
    <article v-for="item in items" :key="item.id" class="code-row">
      <div class="code-head">
        <span class="code">{{ item.code }}</span>
        <span class="badge" :class="item.status">{{ ITEM_STATUS_TEXT[item.status] }}</span>
        <span v-if="item.cached" class="tag-cached" title="图片来自本地图库，未重复下载">图库复用</span>
        <span v-if="item.title" class="title muted" :title="item.title">{{ item.title }}</span>
        <span v-if="item.error && item.status !== 'done'" class="item-err">{{ item.error }}</span>
        <div class="row-actions">
          <a
            v-if="item.detail_url"
            class="ext"
            :href="item.detail_url"
            target="_blank"
            rel="noreferrer noopener"
            >原页 ↗</a
          >
          <button
            class="del-item"
            :disabled="deletingItem === item.id"
            :title="item.status === 'downloading' ? '删除并停止下载' : '删除这一行'"
            @click="removeItem(item)"
          >
            {{ deletingItem === item.id ? '删除中…' : '删除' }}
          </button>
        </div>
      </div>

      <div class="strip" :style="thumbStyle()">
        <template v-if="(imagesByItem.get(item.id) || []).length">
          <figure
            v-for="img in imagesByItem.get(item.id)"
            :key="img.id"
            class="shot"
            :class="{ cover: img.kind === 'cover' }"
          >
            <div class="frame">
              <img
                v-if="img.status === 'done' && img.path"
                :src="mediaUrl(img.path)"
                :alt="item.code"
                loading="lazy"
                @click="openImage(img, item)"
              />
              <div v-else class="ph" :class="img.status" :title="img.error || ''">
                <span v-if="img.status === 'downloading'" class="spinner"></span>
                <span class="ph-label">{{ IMAGE_STATUS_TEXT[img.status] }}</span>
                <span v-if="img.status === 'failed' && img.attempts" class="ph-sub">
                  已试 {{ img.attempts }} 次
                </span>
                <span
                  v-else-if="img.status === 'downloading' && img.attempts > 1"
                  class="ph-sub"
                >
                  第 {{ img.attempts }} 次
                </span>
              </div>
            </div>
            <figcaption>{{ img.kind === 'cover' ? '主图' : img.seq }}</figcaption>
          </figure>
        </template>

        <div v-else class="row-state muted small">
          <template v-if="item.status === 'notfound'">站内没有该番号，未抓取到图片</template>
          <template v-else-if="item.status === 'failed'">处理失败，未抓取到图片</template>
          <template v-else>
            <span class="spinner dark"></span> 正在搜索番号…
          </template>
        </div>
      </div>

      <div v-if="item.link" class="link-row">
        <span class="link-label">下载链接</span>
        <a
          v-if="isHttpLink(item.link)"
          class="link-text"
          :href="item.link"
          target="_blank"
          rel="noreferrer noopener"
          :title="item.link"
          >{{ item.link }}</a
        >
        <span v-else class="link-text" :title="item.link">{{ item.link }}</span>
        <button class="btn btn-sm copy" @click="copyLink(item)">
          {{
            copyState?.id === item.id
              ? copyState.ok
                ? '已复制'
                : '复制失败'
              : '复制'
          }}
        </button>
      </div>
    </article>
    </template>

    <Lightbox
      v-if="lightbox"
      :src="lightbox.src"
      :caption="lightbox.caption"
      @close="lightbox = null"
    />
  </section>
</template>

<style scoped>
.detail {
  padding-bottom: 40px;
}

.film-items {
  background: var(--panel);
  border: 1px solid var(--line);
  border-radius: var(--radius);
  box-shadow: var(--shadow);
  overflow: hidden;
}

.film-row {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 9px 16px;
  border-bottom: 1px solid var(--line);
  font-size: 13px;
  width: 100%;
  text-align: left;
  background: none;
  border-left: 0;
  border-right: 0;
  border-top: 0;
  color: var(--text);
  cursor: pointer;
  transition: background 0.15s;
}

.film-row:hover {
  background: #f6f7fb;
}

.film-jump {
  flex: 0 0 auto;
  font-size: 12px;
  color: var(--accent);
  opacity: 0;
  transition: opacity 0.15s;
  white-space: nowrap;
}

.film-row:hover .film-jump {
  opacity: 1;
}

.film-row:last-child {
  border-bottom: 0;
}

.film-name {
  font-weight: 650;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  flex: 0 1 auto;
}

.film-target {
  flex: 1 1 auto;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 12px;
}

.badge.skipped {
  background: #eef1f7;
  color: #5b6271;
}

.page-head {
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  gap: 24px;
  margin-bottom: 14px;
}

.crumbs {
  display: flex;
  gap: 6px;
  margin-bottom: 6px;
}

.crumbs a:hover {
  color: var(--accent);
}

.slash {
  opacity: 0.4;
}

h1 {
  display: flex;
  align-items: center;
  gap: 10px;
}

.head-actions {
  display: flex;
  align-items: center;
  gap: 10px;
}

.sticky-progress {
  position: sticky;
  top: 56px;
  z-index: 30;
  margin-bottom: 18px;
}

.code-row {
  background: var(--panel);
  border: 1px solid var(--line);
  border-radius: var(--radius);
  box-shadow: var(--shadow);
  margin-bottom: 14px;
  overflow: hidden;
}

.code-head {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 11px 16px;
  border-bottom: 1px solid var(--line);
  min-height: 46px;
}

.code {
  font-family: var(--mono);
  font-size: 14px;
  font-weight: 650;
  letter-spacing: 0.03em;
}

.title {
  font-size: 12.5px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  min-width: 0;
}

.ext {
  font-size: 12px;
  color: var(--faint);
  white-space: nowrap;
}

.ext:hover {
  color: var(--accent);
}

.row-actions {
  margin-left: auto;
  display: flex;
  align-items: center;
  gap: 10px;
  flex: 0 0 auto;
}

.del-item {
  border: 1px solid var(--line);
  background: #fff;
  color: var(--muted);
  border-radius: 6px;
  padding: 3px 9px;
  font-size: 12px;
  line-height: 1.5;
  cursor: pointer;
  transition: all 0.15s;
}

.del-item:hover:not(:disabled) {
  color: var(--err);
  border-color: #f3c4c0;
  background: var(--err-soft);
}

.del-item:disabled {
  opacity: 0.6;
  cursor: default;
}

.link-row {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 9px 16px 11px;
  border-top: 1px dashed var(--line);
  background: #fbfbfd;
}

.link-label {
  flex: 0 0 auto;
  font-size: 11.5px;
  color: var(--faint);
}

.link-text {
  flex: 1 1 auto;
  min-width: 0;
  font-family: var(--mono);
  font-size: 11.5px;
  color: var(--muted);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  text-decoration: none;
}

a.link-text:hover {
  color: var(--accent);
  text-decoration: underline dotted;
}

.copy {
  flex: 0 0 auto;
  padding: 3px 10px;
  font-size: 12px;
}

.item-err {
  font-size: 12px;
  color: var(--err);
}

.tag-cached {
  font-size: 11.5px;
  padding: 1px 7px;
  border-radius: 999px;
  background: #eef1f7;
  color: #5b6271;
  border: 1px solid var(--line);
}

.retry {
  color: var(--err);
  border-color: #f3c4c0;
  background: var(--err-soft);
}

.retry:hover:not(:disabled) {
  background: #fbe0de;
}

.retry-hint {
  margin: -6px 0 14px;
  color: var(--muted);
}

.strip {
  --shot-h: 144px;
  display: flex;
  align-items: flex-start;
  gap: 10px;
  padding: 12px 16px 10px;
  overflow-x: auto;
  overflow-y: hidden;
  scrollbar-width: thin;
}

.strip::-webkit-scrollbar {
  height: 8px;
}

.strip::-webkit-scrollbar-thumb {
  background: #d6d9e1;
  border-radius: 99px;
}

.strip::-webkit-scrollbar-track {
  background: transparent;
}

.shot {
  margin: 0;
  flex: 0 0 auto;
  width: max-content;
}

.frame {
  height: var(--shot-h);
  border-radius: 7px;
  overflow: hidden;
  background: #f0f1f5;
  border: 1px solid var(--line);
  display: flex;
  align-items: center;
  justify-content: center;
}

.shot.cover .frame {
  border-color: #cfd2e0;
  box-shadow: 0 0 0 1px rgba(79, 70, 229, 0.18);
}

.frame img {
  height: 100%;
  width: auto;
  display: block;
  cursor: zoom-in;
  transition: transform 0.25s ease;
}

.frame img:hover {
  transform: scale(1.015);
}

.ph {
  height: 100%;
  width: calc(var(--shot-h) * 1.5);
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 8px;
  background: repeating-linear-gradient(
    135deg,
    #f4f5f8,
    #f4f5f8 8px,
    #eef0f4 8px,
    #eef0f4 16px
  );
}

.ph-label {
  font-size: 12px;
  color: var(--faint);
}

.ph-sub {
  font-size: 11px;
  color: var(--faint);
  opacity: 0.85;
}

.ph.downloading .ph-label {
  color: var(--warn);
}

.ph.failed .ph-label {
  color: var(--err);
}

figcaption {
  margin-top: 5px;
  font-size: 11.5px;
  color: var(--faint);
  text-align: center;
  font-variant-numeric: tabular-nums;
}

.row-state {
  display: flex;
  align-items: center;
  gap: 8px;
  height: var(--shot-h);
  padding-left: 4px;
}

.spinner {
  width: 14px;
  height: 14px;
  border-radius: 50%;
  border: 2px solid #e6c98d;
  border-top-color: var(--warn);
  animation: spin 0.8s linear infinite;
}

.spinner.dark {
  border-color: #d3d6df;
  border-top-color: var(--accent);
}

@keyframes spin {
  to {
    transform: rotate(360deg);
  }
}
</style>
