<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import {
  deleteFilmEntry,
  fetchFilmDirs,
  fetchFilmEntries,
  fetchFilmHandler,
  playUrl,
  renameFilmEntry,
  type FilmDir,
  type FilmEntry,
  type FilmHandler
} from '../api'
import FilmCard from '../components/FilmCard.vue'
import FilmTree from '../components/FilmTree.vue'
import FilmToolDialog from '../components/FilmToolDialog.vue'

// ---------------------------------------------------------------- 视图切换
type ViewMode = 'dirs' | 'category'
const mode = ref<ViewMode>('dirs')
const switcherOpen = ref(false)
const toolOpen = ref(false)
const toolDlg = ref<'organize' | 'thumb' | null>(null)
const startedTip = ref('')
const MODE_TEXT: Record<ViewMode, string> = { dirs: '按目录', category: '按分类' }

function openTool(t: 'organize' | 'thumb') {
  toolOpen.value = false
  toolDlg.value = t
}

function onToolStarted(taskId: number) {
  startedTip.value = `任务 #${taskId} 已启动，进度见任务列表`
  window.setTimeout(() => {
    if (startedTip.value.startsWith(`任务 #${taskId}`)) startedTip.value = ''
  }, 6000)
}

function pickMode(m: ViewMode) {
  mode.value = m
  switcherOpen.value = false
}

// ---------------------------------------------------------------- 数据
const route = useRoute()
const dirs = ref<FilmDir[]>([])
const mapped = ref(false)
const currentDirId = ref<number | null>(null)
const currentPath = ref('')
const entries = ref<FilmEntry[]>([])
const loading = ref(false)
// true = 本次拿到的是 DB 快照，后台扫描完成后经 SSE 推送完整数据（含拼贴）
const scanning = ref(false)
const error = ref('')
const refreshTick = ref(0)
// 从任务详情跳转过来时要高亮的条目名（?pick=）
const pickName = ref('')
const gridRef = ref<HTMLElement | null>(null)

// ---------------------------------------------------------------- 扫描推送（SSE）

interface ScanEventPayload {
  dirId: number
  path: string
  entries: FilmEntry[] | null
  error: string | null
  seq: number
}
const scanEvent = ref<ScanEventPayload | null>(null)
let eventSeq = 0
let stream: EventSource | null = null

function connectStream() {
  stream = new EventSource('/api/films/stream')
  stream.addEventListener('scan', (ev) => {
    try {
      const data = JSON.parse((ev as MessageEvent).data) as {
        dir_id: number
        parent_rel: string
        entries: FilmEntry[] | null
        error?: string
      }
      eventSeq++
      scanEvent.value = {
        dirId: data.dir_id,
        path: data.parent_rel,
        entries: data.entries ?? null,
        error: data.error ?? null,
        seq: eventSeq
      }
      // 只处理当前正在看的级
      if (data.dir_id !== currentDirId.value || data.parent_rel !== currentPath.value) return
      scanning.value = false
      if (data.error) {
        error.value = data.error
        entries.value = []
      } else if (data.entries) {
        entries.value = data.entries
      }
    } catch {
      /* 忽略无法解析的事件 */
    }
  })
}

onBeforeUnmount(() => {
  stream?.close()
  stream = null
})

onMounted(async () => {
  connectStream()
  try {
    const resp = await fetchFilmDirs()
    dirs.value = resp.dirs
    mapped.value = resp.mapped
    // 深链定位：/?dir=<dirId>&path=<rel>&pick=<文件名>（任务详情「查看」跳过来）
    const qdir = Number(route.query.dir)
    const qpath = typeof route.query.path === 'string' ? route.query.path : ''
    const qpick = typeof route.query.pick === 'string' ? route.query.pick : ''
    if (qdir && dirs.value.some((d) => d.id === qdir)) {
      pickName.value = qpick
      await selectDir(qdir, qpath)
      await focusPick()
    } else if (resp.dirs.length) {
      await selectDir(resp.dirs[0].id, '')
    }
  } catch (e) {
    error.value = e instanceof Error ? e.message : '加载影片目录失败'
  }
})

async function selectDir(dirId: number, path: string) {
  currentDirId.value = dirId
  currentPath.value = path
  await reload()
}

/** 高亮 ?pick= 指定的条目并滚动到可见（任务详情跳转用）。 */
async function focusPick() {
  if (!pickName.value || !gridRef.value) return
  await nextTick()
  for (const el of Array.from(gridRef.value.children) as HTMLElement[]) {
    if (el.dataset.name === pickName.value) {
      el.scrollIntoView?.({ block: 'center' })
      el.classList.add('picked-flash')
      window.setTimeout(() => el.classList.remove('picked-flash'), 4000)
      break
    }
  }
  pickName.value = ''
}

async function reload(force = false) {
  if (currentDirId.value === null) return
  loading.value = true
  error.value = ''
  try {
    const resp = await fetchFilmEntries(currentDirId.value, currentPath.value, force)
    entries.value = resp.entries
    scanning.value = resp.scanning
  } catch (e) {
    entries.value = []
    scanning.value = false
    error.value = e instanceof Error ? e.message : '加载目录失败'
  } finally {
    loading.value = false
  }
}

/** 右上角刷新：force=1 绕过服务端 TTL，强制真实读盘 */
async function refresh() {
  refreshTick.value++
  await reload(true)
}

const currentDir = computed(() => dirs.value.find((d) => d.id === currentDirId.value) ?? null)

const crumbs = computed(() => {
  const parts = currentPath.value ? currentPath.value.split('/').filter(Boolean) : []
  let acc = ''
  return parts.map((p) => {
    acc = acc ? `${acc}/${p}` : p
    return { name: p, path: acc }
  })
})

// ---------------------------------------------------------------- 本机播放
const showInstall = ref(false)
const handler = ref<FilmHandler | null>(null)

function openEntry(entry: FilmEntry) {
  if (!entry.local_path) {
    error.value = '该文件没有可用的本机路径（未配置 APHRODITE_FILM_PATH_MAP）'
    return
  }
  error.value = ''
  let responded = false
  let frame: HTMLIFrameElement | null = null
  const onActivity = () => {
    responded = true
  }
  const cleanup = () => {
    window.removeEventListener('blur', onActivity)
    document.removeEventListener('visibilitychange', onActivity)
    frame?.remove()
  }
  window.addEventListener('blur', onActivity)
  document.addEventListener('visibilitychange', onActivity)
  // 隐藏 iframe 触发协议，避免页面被导航
  frame = document.createElement('iframe')
  frame.style.display = 'none'
  frame.src = playUrl(entry.local_path)
  document.body.appendChild(frame)
  // 启发式回退：1.5s 内页面没有失焦 → 视为本机没有协议处理器，弹安装提示
  setTimeout(() => {
    cleanup()
    if (!responded) void openInstall()
  }, 1500)
}

async function openInstall() {
  showInstall.value = true
  if (!handler.value) {
    try {
      handler.value = await fetchFilmHandler()
    } catch (e) {
      error.value = e instanceof Error ? e.message : '获取安装脚本失败'
    }
  }
}

function downloadPs1() {
  if (!handler.value) return
  const blob = new Blob([handler.value.ps1], { type: 'text/plain;charset=utf-8' })
  const a = document.createElement('a')
  a.href = URL.createObjectURL(blob)
  a.download = 'install.ps1'
  a.click()
  URL.revokeObjectURL(a.href)
}

async function copyText(text: string) {
  await navigator.clipboard.writeText(text)
}

// ---------------------------------------------------------------- 重命名 / 删除
const renaming = ref<FilmEntry | null>(null)
const renameName = ref('')
const renameBusy = ref(false)
const deleting = ref<FilmEntry | null>(null)
const deleteBusy = ref(false)

function startRename(entry: FilmEntry) {
  renaming.value = entry
  renameName.value = entry.name
}

async function doRename() {
  if (!renaming.value || !renameName.value.trim()) return
  renameBusy.value = true
  error.value = ''
  try {
    await renameFilmEntry(renaming.value.id, renameName.value.trim())
    renaming.value = null
    await reload()
    refreshTick.value++
  } catch (e) {
    error.value = e instanceof Error ? e.message : '重命名失败'
  } finally {
    renameBusy.value = false
  }
}

function startDelete(entry: FilmEntry) {
  deleting.value = entry
}

async function doDelete() {
  if (!deleting.value) return
  deleteBusy.value = true
  error.value = ''
  try {
    await deleteFilmEntry(deleting.value.id)
    deleting.value = null
    await reload()
    refreshTick.value++
  } catch (e) {
    error.value = e instanceof Error ? e.message : '删除失败'
  } finally {
    deleteBusy.value = false
  }
}
</script>

<template>
  <section class="films-page">
    <div class="films-head">
      <div>
        <h1>影片管理</h1>
        <p class="muted small">
          管理本地影片目录。目录由环境变量 <code>APHRODITE_FILM_DIRS</code> 配置。
        </p>
      </div>
      <div class="head-actions">
        <p v-if="startedTip" class="started-tip small">{{ startedTip }}</p>
        <div class="view-switch" @mouseleave="switcherOpen = false">
          <button class="btn btn-sm" @click="switcherOpen = !switcherOpen">
            {{ MODE_TEXT[mode] }} ▾
          </button>
          <div v-if="switcherOpen" class="switch-menu">
            <button
              v-for="(text, m) in MODE_TEXT"
              :key="m"
              class="switch-item"
              :class="{ active: mode === m }"
              @click="pickMode(m as ViewMode)"
            >
              {{ text }}<span v-if="m === 'category'" class="muted small"> · 开发中</span>
            </button>
          </div>
        </div>
        <div class="view-switch" @mouseleave="toolOpen = false">
          <button class="btn btn-sm" @click="toolOpen = !toolOpen">工具 ▾</button>
          <div v-if="toolOpen" class="switch-menu">
            <button class="switch-item tool-item" @click="openTool('organize')">
              <span class="tool-name">整理番号目录</span>
              <span class="muted small">改名建目录 + 归集图片</span>
            </button>
            <button class="switch-item tool-item" @click="openTool('thumb')">
              <span class="tool-name">补全缩略图</span>
              <span class="muted small">ffmpeg 抽帧生成</span>
            </button>
          </div>
        </div>
      </div>
    </div>

    <FilmToolDialog
      :open="toolDlg !== null"
      :tool="toolDlg ?? 'organize'"
      :dirs="dirs"
      :current-dir-id="currentDirId"
      :current-path="currentPath"
      @close="toolDlg = null"
      @started="onToolStarted"
    />

    <div v-if="mode === 'category'" class="card empty">分类视图开发中，敬请期待。</div>

    <div v-else class="films-body">
      <FilmTree
        :dirs="dirs"
        :current-dir-id="currentDirId"
        :current-path="currentPath"
        :refresh-tick="refreshTick"
        :scan-event="scanEvent"
        @select="selectDir"
      />

      <div class="films-main">
        <div class="crumbs-bar">
          <nav v-if="currentDir" class="crumbs">
            <button class="crumb" @click="selectDir(currentDir.id, '')">
              📁 {{ currentDir.label || currentDir.path }}
            </button>
            <template v-for="c in crumbs" :key="c.path">
              <span class="crumb-sep">/</span>
              <button
                class="crumb"
                :class="{ current: c.path === currentPath }"
                @click="selectDir(currentDir.id, c.path)"
              >
                {{ c.name }}
              </button>
            </template>
          </nav>
          <span v-if="scanning && entries.length" class="muted small">同步中…</span>
          <button class="btn btn-sm ghost" :disabled="loading" @click="refresh">
            {{ loading ? '加载中…' : '↻ 刷新' }}
          </button>
        </div>

        <p v-if="error" class="err">{{ error }}</p>

        <div v-if="!dirs.length" class="card empty">
          还没有影片目录。请在环境变量 <code>APHRODITE_FILM_DIRS</code> 中配置，
          并在 docker-compose 里把对应目录挂载进容器。
        </div>

        <div v-else-if="loading && !entries.length" class="card empty">加载中…</div>
        <div v-else-if="scanning && !entries.length" class="card empty">
          正在扫描磁盘（USB 硬盘可能需要几秒到十几秒）…
        </div>
        <div v-else-if="!entries.length" class="card empty">此目录为空。</div>

        <div v-else ref="gridRef" class="grid">
          <FilmCard
            v-for="e in entries"
            :key="e.id"
            :entry="e"
            :dir-id="currentDirId!"
            :data-name="e.name"
            @navigate="selectDir(currentDirId!, $event.rel_path)"
            @open="openEntry"
            @rename="startRename"
            @delete="startDelete"
          />
        </div>
      </div>
    </div>

    <!-- 重命名弹窗 -->
    <div v-if="renaming" class="overlay" @click.self="renaming = null">
      <div class="modal">
        <h3>重命名</h3>
        <p class="muted small">同名缩略图会一起改名；改名后按新文件名重新提取番号。</p>
        <input v-model="renameName" class="rename-input" @keydown.enter="doRename" />
        <div class="modal-actions">
          <button class="btn btn-sm ghost" @click="renaming = null">取消</button>
          <button
            class="btn btn-primary btn-sm"
            :disabled="renameBusy || !renameName.trim()"
            @click="doRename"
          >
            {{ renameBusy ? '保存中…' : '保存' }}
          </button>
        </div>
      </div>
    </div>

    <!-- 删除确认弹窗 -->
    <div v-if="deleting" class="overlay" @click.self="deleting = null">
      <div class="modal">
        <h3>确认删除</h3>
        <p class="delete-name mono">{{ deleting.name }}</p>
        <p class="muted small">
          {{
            deleting.is_dir
              ? '这是一个文件夹，其中的全部内容（包括子文件夹）将被永久删除。'
              : '该文件将从磁盘上永久删除，此操作不可恢复。'
          }}
        </p>
        <div class="modal-actions">
          <button class="btn btn-sm ghost" @click="deleting = null">取消</button>
          <button class="btn btn-sm danger-solid" :disabled="deleteBusy" @click="doDelete">
            {{ deleteBusy ? '删除中…' : '永久删除' }}
          </button>
        </div>
      </div>
    </div>

    <!-- 协议处理器安装提示 -->
    <div v-if="showInstall" class="overlay" @click.self="showInstall = false">
      <div class="modal install">
        <h3>尚未检测到本机播放处理器</h3>
        <p class="muted small">
          「打开」需要本机注册 <code>aphrodite-play://</code> 协议（一次性操作）。
          下载或复制安装脚本，在 PowerShell 里运行即可。
        </p>
        <div v-if="handler" class="install-actions">
          <button class="btn btn-primary btn-sm" @click="downloadPs1">下载 install.ps1</button>
          <button class="btn btn-sm" @click="copyText(handler.ps1)">复制脚本内容</button>
          <button class="btn btn-sm" @click="copyText(handler.readme)">复制安装说明</button>
        </div>
        <pre v-if="handler" class="handler-preview">{{ handler.readme }}</pre>
        <div class="modal-actions">
          <button class="btn btn-sm ghost" @click="showInstall = false">关闭</button>
        </div>
      </div>
    </div>
  </section>
</template>

<style scoped>
.films-body {
  display: flex;
  gap: 18px;
  align-items: flex-start;
}

.films-main {
  flex: 1;
  min-width: 0;
}

.films-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  margin-bottom: 18px;
}

.films-head code {
  font-family: var(--mono);
  font-size: 12px;
  background: #eceef4;
  border-radius: 5px;
  padding: 1px 5px;
}

/* 视图切换下拉 */
.view-switch {
  position: relative;
}

.head-actions {
  display: flex;
  align-items: center;
  gap: 10px;
}

.started-tip {
  color: var(--accent);
  margin: 0;
  white-space: nowrap;
}

.tool-item {
  display: grid;
  gap: 1px;
  min-width: 190px;
}

.tool-name {
  font-weight: 650;
}

.switch-menu {
  position: absolute;
  right: 0;
  top: calc(100% + 4px);
  background: var(--panel);
  border: 1px solid var(--line);
  border-radius: 10px;
  box-shadow: var(--shadow);
  padding: 4px;
  display: grid;
  gap: 2px;
  min-width: 140px;
  z-index: 30;
}

.switch-item {
  border: 0;
  background: none;
  text-align: left;
  padding: 7px 10px;
  border-radius: 7px;
  font-size: 13px;
  cursor: pointer;
  color: var(--text);
}

.switch-item:hover {
  background: #f1f2f6;
}

.switch-item.active {
  background: var(--accent-soft);
  color: var(--accent);
}

/* 面包屑 */
.crumbs-bar {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 14px;
}

.crumbs {
  display: flex;
  align-items: center;
  gap: 4px;
  min-width: 0;
  flex: 1;
  flex-wrap: wrap;
}

.crumb {
  border: 0;
  background: none;
  padding: 3px 6px;
  border-radius: 6px;
  font-size: 13px;
  color: var(--accent);
  cursor: pointer;
  max-width: 220px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.crumb:hover {
  background: var(--accent-soft);
}

.crumb.current {
  color: var(--text);
  font-weight: 550;
  cursor: default;
}

.crumb.current:hover {
  background: none;
}

.crumb-sep {
  color: var(--faint);
}

.ghost {
  border-color: transparent;
  color: var(--muted);
  background: transparent;
}

/* 网格 */
.grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(200px, 1fr));
  gap: 14px;
}

/* 任务详情跳转过来的条目高亮 */
.grid > .picked-flash {
  outline: 2px solid var(--accent);
  outline-offset: 3px;
  border-radius: var(--radius);
}

.err {
  color: var(--err);
  font-size: 13px;
  margin: 0 0 12px;
}

/* 弹窗 */
.overlay {
  position: fixed;
  inset: 0;
  background: rgba(15, 18, 28, 0.45);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 100;
}

.modal {
  background: var(--panel);
  border-radius: 14px;
  box-shadow: var(--shadow);
  padding: 22px 24px;
  width: 420px;
  max-width: calc(100vw - 48px);
  max-height: 80vh;
  overflow-y: auto;
}

.modal h3 {
  margin: 0 0 8px;
  font-size: 16px;
}

.rename-input {
  width: 100%;
  border: 1px solid var(--line);
  border-radius: 8px;
  padding: 8px 10px;
  font-size: 13.5px;
  margin-top: 10px;
  outline: none;
}

.rename-input:focus {
  border-color: var(--accent);
}

.delete-name {
  font-size: 13px;
  background: #f4f5f8;
  border-radius: 8px;
  padding: 8px 10px;
  word-break: break-all;
  margin: 8px 0;
}

.modal-actions {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
  margin-top: 16px;
}

.danger-solid {
  background: var(--err);
  border-color: var(--err);
  color: #fff;
}

/* 安装提示 */
.install-actions {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
  margin: 12px 0;
}

.handler-preview {
  background: #f4f5f8;
  border-radius: 8px;
  padding: 10px 12px;
  font-size: 11.5px;
  line-height: 1.6;
  font-family: var(--mono);
  white-space: pre-wrap;
  word-break: break-all;
  max-height: 180px;
  overflow-y: auto;
}
</style>
