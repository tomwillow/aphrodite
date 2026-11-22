<script setup lang="ts">
import { computed, nextTick, reactive, ref, watch } from 'vue'
import { fetchFilmEntries, type FilmDir, type FilmEntry } from '../api'

interface Child {
  path: string
  name: string
}

interface NodeState {
  dirId: number
  path: string
  /** null = 还没加载过子级 */
  children: Child[] | null
  expanded: boolean
  loading: boolean
}

interface Row {
  key: string
  dirId: number
  path: string
  name: string
  depth: number
  root: boolean
  /** 「没有子文件夹」占位行，不可点、不可展开 */
  placeholder?: boolean
}

const props = defineProps<{
  dirs: FilmDir[]
  currentDirId: number | null
  currentPath: string
  refreshTick: number
  /** 后台扫描推送（LibraryView 转发）：命中已展开节点时直接用载荷更新子级 */
  scanEvent?: { dirId: number; path: string; entries: FilmEntry[] | null; seq: number } | null
}>()

const emit = defineEmits<{
  (e: 'select', dirId: number, path: string): void
}>()

const states = reactive<Record<string, NodeState>>({})
const host = ref<HTMLElement | null>(null)

const keyOf = (dirId: number, path: string) => `${dirId}::${path}`

function stateOf(dirId: number, path: string): NodeState {
  const k = keyOf(dirId, path)
  if (!states[k]) states[k] = { dirId, path, children: null, expanded: false, loading: false }
  return states[k]
}

function peekState(dirId: number, path: string): NodeState | undefined {
  return states[keyOf(dirId, path)]
}

// ---------------------------------------------------------------- 扁平化渲染
// 用「状态表 + 计算扁平行列表」实现递归树：任意深度都能展开，
// 比递归组件更容易测（没有子组件挂载开销）。

const rows = computed<Row[]>(() => {
  const out: Row[] = []
  for (const d of props.dirs) {
    out.push({
      key: keyOf(d.id, ''),
      dirId: d.id,
      path: '',
      name: d.label || d.path,
      depth: 0,
      root: true
    })
    appendChildren(out, d.id, '', 1)
  }
  return out
})

function appendChildren(out: Row[], dirId: number, path: string, depth: number) {
  const st = peekState(dirId, path)
  if (!st?.expanded || !st.children) return
  if (!st.children.length) {
    out.push({
      key: `${keyOf(dirId, path)}::empty`,
      dirId,
      path,
      name: '没有子文件夹',
      depth,
      root: false,
      placeholder: true
    })
    return
  }
  for (const c of st.children) {
    out.push({
      key: keyOf(dirId, c.path),
      dirId,
      path: c.path,
      name: c.name,
      depth,
      root: false
    })
    appendChildren(out, dirId, c.path, depth + 1)
  }
}

function toChildren(entries: FilmEntry[]): Child[] {
  return entries.filter((e) => e.is_dir).map((e) => ({ path: e.rel_path, name: e.name }))
}

async function load(dirId: number, path: string) {
  const st = stateOf(dirId, path)
  if (st.loading) return
  st.loading = true
  try {
    const resp = await fetchFilmEntries(dirId, path)
    st.children = toChildren(resp.entries)
  } catch {
    st.children = []
  } finally {
    st.loading = false
  }
}

async function toggle(row: Row) {
  if (row.placeholder) return
  const st = stateOf(row.dirId, row.path)
  st.expanded = !st.expanded
  if (st.expanded && st.children === null) await load(row.dirId, row.path)
}

function selectDir(row: Row) {
  if (row.placeholder) return
  emit('select', row.dirId, row.path)
}

function isActive(row: Row) {
  return row.dirId === props.currentDirId && row.path === props.currentPath
}

function isExpanded(row: Row) {
  return !!peekState(row.dirId, row.path)?.expanded
}

function isLoading(row: Row) {
  return !!peekState(row.dirId, row.path)?.loading
}

// ---------------------------------------------------------------- 跟随当前目录展开

let revealSeq = 0

/** 展开当前目录的所有祖先节点，并把当前行滚进可视区。 */
async function reveal() {
  const dirId = props.currentDirId
  if (dirId === null) return
  const seq = ++revealSeq
  const chain = ['']
  let acc = ''
  for (const part of props.currentPath.split('/').filter(Boolean)) {
    acc = acc ? `${acc}/${part}` : part
    chain.push(acc)
  }
  for (const path of chain) {
    const st = stateOf(dirId, path)
    st.expanded = true
    if (st.children === null) {
      await load(dirId, path)
      if (seq !== revealSeq) return // 期间又切了目录，交给后一次 reveal
    }
  }
  await nextTick()
  if (seq !== revealSeq) return
  const el = host.value?.querySelector('.tree-row.active') as HTMLElement | null
  if (el && typeof el.scrollIntoView === 'function') el.scrollIntoView({ block: 'nearest' })
}

watch(
  () => [props.currentDirId, props.currentPath, props.dirs] as const,
  () => void reveal(),
  { immediate: true }
)

// 手动刷新（force 读盘）：已展开的节点全部重新拉一次
watch(
  () => props.refreshTick,
  async () => {
    const targets = Object.values(states).filter((st) => st.expanded)
    await Promise.all(targets.map((st) => load(st.dirId, st.path)))
  }
)

// 后台扫描完成：对应级的子文件夹列表可能变了，直接用事件载荷更新
watch(
  () => props.scanEvent,
  (evt) => {
    if (!evt || !evt.entries) return
    const st = peekState(evt.dirId, evt.path)
    if (st?.expanded) st.children = toChildren(evt.entries)
  }
)
</script>

<template>
  <aside ref="host" class="film-tree">
    <div v-if="!dirs.length" class="tree-empty muted small">未配置影片目录</div>
    <ul v-else class="tree-roots">
      <li v-for="row in rows" :key="row.key">
        <div
          class="tree-row"
          :class="{ root: row.root, active: isActive(row), placeholder: row.placeholder }"
          :style="{ paddingLeft: 6 + row.depth * 14 + 'px' }"
          @click="selectDir(row)"
        >
          <button
            v-if="!row.placeholder"
            class="tree-toggle"
            :class="{ open: isExpanded(row), busy: isLoading(row) }"
            @click.stop="toggle(row)"
          >
            {{ isLoading(row) ? '…' : isExpanded(row) ? '▾' : '▸' }}
          </button>
          <span class="tree-name">
            <template v-if="!row.placeholder">{{ row.root ? '📁' : '🗂' }} {{ row.name }}</template>
            <template v-else>{{ row.name }}</template>
          </span>
        </div>
      </li>
    </ul>
  </aside>
</template>

<style scoped>
.film-tree {
  width: 240px;
  flex: 0 0 auto;
  background: var(--panel);
  border: 1px solid var(--line);
  border-radius: var(--radius);
  box-shadow: var(--shadow);
  padding: 10px 8px;
  align-self: flex-start;
  max-height: calc(100vh - 200px);
  overflow-y: auto;
}

ul {
  list-style: none;
  margin: 0;
  padding: 0;
}

.tree-roots {
  display: grid;
  gap: 2px;
}

.tree-row {
  display: flex;
  align-items: center;
  gap: 4px;
  padding: 5px 8px;
  border-radius: 7px;
  cursor: pointer;
  font-size: 12.5px;
  color: var(--text);
  user-select: none;
}

.tree-row:hover {
  background: #f1f2f6;
}

.tree-row.active {
  background: var(--accent-soft);
  color: var(--accent);
  font-weight: 550;
}

.tree-row.root {
  font-weight: 550;
  font-size: 13px;
}

.tree-row.placeholder {
  cursor: default;
  color: var(--faint);
  font-size: 12px;
}

.tree-row.placeholder:hover {
  background: none;
}

.tree-toggle {
  border: 0;
  background: none;
  padding: 0 2px;
  width: 16px;
  color: var(--faint);
  cursor: pointer;
  font-size: 11px;
  flex: 0 0 auto;
}

.tree-name {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.tree-empty {
  padding: 8px;
}
</style>
