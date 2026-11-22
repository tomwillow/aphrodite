<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import {
  cancelTask,
  deleteTask,
  fetchTasks,
  retryFailedItems,
  type Task
} from '../api'
import { TASK_KIND_TEXT, TASK_STATUS_TEXT, relTime } from '../lib/status'

const router = useRouter()
const tasks = ref<Task[]>([])
const loading = ref(true)
const error = ref('')
const deleting = ref<number | null>(null)
const acting = ref<number | null>(null)
const notice = ref('')
let timer: number | undefined

const runningCount = computed(() => tasks.value.filter((t) => t.status === 'running').length)

function kindOf(t: Task) {
  return t.kind ?? 'scrape'
}

function isFilmTask(t: Task) {
  const k = kindOf(t)
  return k === 'organize' || k === 'thumb'
}

function filmSub(t: Task): string {
  const s = t.summary
  if (!s) return ''
  const parts: string[] = []
  if (s.skipped) parts.push(`跳过 ${s.skipped}`)
  if (s.failed) parts.push(`失败 ${s.failed}`)
  return parts.length ? ` · ${parts.join(' · ')}` : ''
}

function canRetry(t: Task) {
  return isFilmTask(t) && (t.status === 'completed' || t.status === 'failed') && !!t.summary?.failed
}

function canCancel(t: Task) {
  return isFilmTask(t) && (t.status === 'running' || t.status === 'pending')
}

async function doCancel(t: Task) {
  acting.value = t.id
  try {
    await cancelTask(t.id)
    await load()
  } catch (e) {
    error.value = e instanceof Error ? e.message : '取消失败'
  } finally {
    acting.value = null
  }
}

async function doRetry(t: Task) {
  acting.value = t.id
  try {
    const newId = await retryFailedItems(t.id)
    notice.value = `已生成重试任务 #${newId}`
    await load()
  } catch (e) {
    error.value = e instanceof Error ? e.message : '重试失败'
  } finally {
    acting.value = null
  }
}

async function load() {
  try {
    tasks.value = await fetchTasks()
    error.value = ''
  } catch (e) {
    error.value = e instanceof Error ? e.message : '加载失败'
  } finally {
    loading.value = false
  }
}

function open(id: number) {
  router.push(`/tasks/${id}`)
}

async function remove(t: Task) {
  const running = t.status === 'running' || t.status === 'pending'
  const tip = running
    ? `任务 #${t.id} 正在执行，删除会立即取消下载。\n已下载的图片会保留在本地图库中。确定删除？`
    : `删除任务 #${t.id}？\n已下载的图片会保留在本地图库中。`
  if (!window.confirm(tip)) return
  deleting.value = t.id
  try {
    await deleteTask(t.id)
    tasks.value = tasks.value.filter((x) => x.id !== t.id)
  } catch (e) {
    error.value = e instanceof Error ? e.message : '删除失败'
  } finally {
    deleting.value = null
  }
}

function schedule() {
  timer = window.setInterval(() => {
    if (document.visibilityState === 'visible') load()
  }, 1800)
}

onMounted(() => {
  load()
  schedule()
})

onBeforeUnmount(() => {
  if (timer) window.clearInterval(timer)
})

function progressPct(t: Task) {
  if (!t.total) return 0
  return Math.round((t.finished / t.total) * 100)
}
</script>

<template>
  <section>
    <div class="page-head">
      <div>
        <h1>任务列表</h1>
        <p class="muted small">
          共 {{ tasks.length }} 个任务<span v-if="runningCount"> · {{ runningCount }} 个执行中</span>
        </p>
      </div>
      <router-link to="/capture" class="btn btn-primary btn-sm">新建任务</router-link>
    </div>

    <p v-if="error" class="err small">{{ error }}</p>
    <p v-if="notice" class="notice small">{{ notice }}</p>

    <div v-if="loading" class="card empty">加载中…</div>

    <div v-else-if="!tasks.length" class="card empty">
      还没有任务，<router-link to="/capture" class="accent-link">去创建一个</router-link>
    </div>

    <div v-else class="card list">
      <div v-for="t in tasks" :key="t.id" class="row" @click="open(t.id)">
        <div class="col-id">
          <div>#{{ t.id }}</div>
          <span class="kind-badge" :class="kindOf(t)">{{ TASK_KIND_TEXT[kindOf(t)] }}</span>
        </div>
        <div class="col-main">
          <div class="main-line">
            <span class="task-label">{{ t.raw_text }}</span>
            <span class="badge" :class="t.status">
              <span v-if="t.status === 'running' || t.status === 'pending'" class="pulse"></span>
              {{ TASK_STATUS_TEXT[t.status] }}
            </span>
          </div>
          <div class="progress">
            <div class="bar" :style="{ width: progressPct(t) + '%' }"></div>
          </div>
          <div class="sub muted small">
            {{ t.finished }} / {{ t.total }} 条已处理{{ filmSub(t) }}
            <span v-if="t.error"> · {{ t.error }}</span>
          </div>
        </div>
        <div class="col-time muted small">{{ relTime(t.created_at) }}</div>
        <div class="col-actions">
          <button
            v-if="canCancel(t)"
            class="del"
            :disabled="acting === t.id"
            @click.stop="doCancel(t)"
          >
            取消
          </button>
          <button
            v-if="canRetry(t)"
            class="del retry"
            :disabled="acting === t.id"
            title="生成新任务，仅处理失败条目"
            @click.stop="doRetry(t)"
          >
            重试
          </button>
          <button
            class="del"
            :disabled="deleting === t.id"
            :title="t.status === 'running' ? '删除并取消下载' : '删除任务'"
            @click.stop="remove(t)"
          >
            {{ deleting === t.id ? '删除中…' : '删除' }}
          </button>
        </div>
        <div class="col-go muted">→</div>
      </div>
    </div>
  </section>
</template>

<style scoped>
.page-head {
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 18px;
}

.err {
  color: var(--err);
}

.accent-link {
  color: var(--accent);
  text-decoration: underline;
}

.list {
  overflow: hidden;
}

.row {
  display: grid;
  grid-template-columns: 92px minmax(0, 1fr) 110px 150px 20px;
  align-items: center;
  gap: 18px;
  padding: 15px 20px;
  border-bottom: 1px solid var(--line);
  transition: background 0.15s;
  cursor: pointer;
}

.kind-badge {
  display: inline-block;
  margin-top: 4px;
  font-size: 10.5px;
  line-height: 1;
  padding: 3px 7px;
  border-radius: 999px;
  white-space: nowrap;
}

.kind-badge.scrape {
  background: var(--line, #eef0f5);
  color: var(--muted);
}

.kind-badge.organize {
  background: var(--accent-soft, #e6f1fb);
  color: var(--accent);
}

.kind-badge.thumb {
  background: var(--ok-soft, #eaf3de);
  color: var(--ok, #3b6d11);
}

.task-label {
  font-size: 14px;
  font-weight: 650;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.col-actions {
  display: flex;
  gap: 6px;
  justify-content: flex-end;
}

.del.retry {
  color: var(--accent);
  border-color: var(--accent-soft, #cfe3f7);
}

.notice {
  color: var(--accent);
}

.row:last-child {
  border-bottom: 0;
}

.row:hover {
  background: #fafbfc;
}

.del {
  height: 28px;
  padding: 0 10px;
  border-radius: 7px;
  border: 1px solid var(--line-strong);
  background: #fff;
  color: var(--muted);
  font-size: 12.5px;
  opacity: 0;
  transition: opacity 0.15s, color 0.15s, border-color 0.15s, background 0.15s;
}

.row:hover .del,
.del:disabled {
  opacity: 1;
}

.del:hover {
  color: var(--err);
  border-color: #f3c4c0;
  background: var(--err-soft);
}

.del:disabled {
  cursor: progress;
  opacity: 0.6;
}

.col-id {
  font-family: var(--mono);
  font-size: 13px;
  color: var(--muted);
}

.main-line {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 7px;
}

.count {
  font-size: 15px;
  font-weight: 650;
  font-variant-numeric: tabular-nums;
}

.progress {
  max-width: 520px;
}

.sub {
  margin-top: 6px;
}

.col-time {
  text-align: right;
  white-space: nowrap;
}

.col-go {
  text-align: right;
  opacity: 0.4;
}

.row:hover .col-go {
  opacity: 1;
  color: var(--accent);
}

/* ---------------------------------------------------------------- 窄屏 */
@media (max-width: 760px) {
  .page-head {
    align-items: flex-start;
    gap: 12px;
    flex-wrap: wrap;
  }

  /* 五列网格在手机上会挤成一条，改成三行卡片式 */
  .row {
    grid-template-columns: minmax(0, 1fr) auto;
    grid-template-areas:
      'id time'
      'main main'
      'actions actions';
    gap: 8px 12px;
    padding: 12px 14px;
  }

  .col-id {
    grid-area: id;
    display: flex;
    align-items: center;
    gap: 8px;
  }

  .kind-badge {
    margin-top: 0;
  }

  .col-main {
    grid-area: main;
  }

  .col-time {
    grid-area: time;
    align-self: center;
  }

  .col-actions {
    grid-area: actions;
    justify-content: flex-start;
  }

  .col-go {
    display: none;
  }

  /* 触摸设备没有 hover，操作按钮直接常显 */
  .del {
    opacity: 1;
    height: 30px;
  }

  .main-line {
    flex-wrap: wrap;
  }

  .task-label {
    white-space: normal;
    word-break: break-all;
  }
}
</style>
