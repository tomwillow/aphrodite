<script setup lang="ts">
import { ref, watch } from 'vue'
import { fetchFilmEntries, type FilmDir } from '../api'

interface TreeNode {
  dirId: number
  path: string
  name: string
  children: TreeNode[] | null
  expanded: boolean
}

const props = defineProps<{
  dirs: FilmDir[]
  currentDirId: number | null
  currentPath: string
  refreshTick: number
}>()

const emit = defineEmits<{
  (e: 'select', dirId: number, path: string): void
}>()

const roots = ref<TreeNode[]>([])
const loading = ref(-1) // 正在展开的 dirId

function buildRoots(dirs: FilmDir[]): TreeNode[] {
  return dirs.map((d) => ({
    dirId: d.id,
    path: '',
    name: d.label || d.path,
    children: null,
    expanded: false
  }))
}

watch(
  () => [props.dirs, props.refreshTick],
  () => {
    const prev = roots.value
    roots.value = buildRoots(props.dirs)
    // 刷新时保留展开状态并重新加载子级；目录列表变化则重建
    if (props.refreshTick > 0 && prev.length === props.dirs.length) {
      for (let i = 0; i < prev.length && i < roots.value.length; i++) {
        if (prev[i].dirId !== roots.value[i].dirId) continue
        roots.value[i].expanded = prev[i].expanded
        roots.value[i].children = prev[i].children
        if (prev[i].expanded) void load(roots.value[i])
      }
    }
  },
  { immediate: true, deep: false }
)

async function load(node: TreeNode) {
  loading.value = node.dirId
  try {
    const resp = await fetchFilmEntries(node.dirId, node.path)
    node.children = resp.entries
      .filter((e) => e.is_dir)
      .map((e) => ({
        dirId: node.dirId,
        path: e.rel_path,
        name: e.name,
        children: null,
        expanded: false
      }))
  } catch {
    node.children = []
  } finally {
    loading.value = -1
  }
}

async function toggle(node: TreeNode) {
  node.expanded = !node.expanded
  if (node.expanded && node.children === null) await load(node)
}

function selectDir(node: TreeNode) {
  emit('select', node.dirId, node.path)
}
</script>

<template>
  <aside class="film-tree">
    <div v-if="!dirs.length" class="tree-empty muted small">未配置影片目录</div>
    <ul v-else class="tree-roots">
      <li v-for="root in roots" :key="root.dirId">
        <div
          class="tree-row root"
          :class="{ active: currentDirId === root.dirId && !currentPath }"
          @click="selectDir(root)"
        >
          <button
            class="tree-toggle"
            :class="{ open: root.expanded, busy: loading === root.dirId }"
            @click.stop="toggle(root)"
          >
            {{ loading === root.dirId ? '…' : root.expanded ? '▾' : '▸' }}
          </button>
          <span class="tree-name">📁 {{ root.name }}</span>
        </div>
        <ul v-if="root.expanded && root.children" class="tree-children">
          <li v-if="!root.children.length" class="muted small tree-empty-child">没有子文件夹</li>
          <li v-for="child in root.children" :key="child.dirId + child.path">
            <div
              class="tree-row"
              :class="{ active: currentDirId === child.dirId && currentPath === child.path }"
              @click="selectDir(child)"
            >
              <span class="tree-name">🗂 {{ child.name }}</span>
            </div>
          </li>
        </ul>
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
  font-size: 13px;
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
}

.tree-children {
  padding-left: 18px;
}

.tree-children .tree-row {
  font-size: 12.5px;
  padding: 4px 8px;
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

.tree-empty-child {
  padding: 4px 10px;
}
</style>
