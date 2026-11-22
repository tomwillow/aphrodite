<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { filmMediaUrl, type FilmEntry } from '../api'
import { fmtTime } from '../lib/status'

const props = defineProps<{
  entry: FilmEntry
  dirId: number
}>()

const emit = defineEmits<{
  (e: 'navigate', entry: FilmEntry): void
  (e: 'open', entry: FilmEntry): void
  (e: 'preview', entry: FilmEntry): void
  (e: 'rename', entry: FilmEntry): void
  (e: 'delete', entry: FilmEntry): void
}>()

const kind = computed(() => props.entry.kind ?? (props.entry.is_dir ? 'dir' : 'video'))
const isFolder = computed(() => props.entry.is_dir)
// 单视频文件夹：按「视频卡片」样式展示，但套一层文件夹轮廓
const isSingleVideoFolder = computed(
  () => isFolder.value && props.entry.video_count === 1
)
const isMultiVideoFolder = computed(
  () => isFolder.value && (props.entry.video_count ?? 0) > 1
)

const thumbUrl = computed(() => {
  const t = props.entry.thumb_rel
  return t ? filmMediaUrl(props.dirId, t) : ''
})

// 单视频文件夹：thumb_rel 恒为空，缩略图取拼贴列表里的第一个（即该视频的同名缩略图）
const folderThumbUrl = computed(() => {
  const t = props.entry.collage?.[0]
  return t ? filmMediaUrl(props.dirId, t) : ''
})

const collageUrls = computed(() =>
  isMultiVideoFolder.value
    ? (props.entry.collage ?? []).map((p) => filmMediaUrl(props.dirId, p))
    : []
)

// 图片加载失败（文件被删/被锁）时退回占位图标，避免露出浏览器破图
const thumbFailed = ref(false)
const folderThumbFailed = ref(false)
const failedCollage = ref<number[]>([])
watch(
  () => [props.entry.thumb_rel, props.entry.collage],
  () => {
    thumbFailed.value = false
    folderThumbFailed.value = false
    failedCollage.value = []
  }
)

const showThumb = computed(() => !!thumbUrl.value && !thumbFailed.value)
const showFolderThumb = computed(
  () => !!folderThumbUrl.value && !folderThumbFailed.value
)
const collageItems = computed(() =>
  collageUrls.value
    .map((url, i) => ({ url, i }))
    .filter((it) => !failedCollage.value.includes(it.i))
)
// 拼贴补位：只有已有图片时才补满 2x2；一张都没有就直接显示文件夹图标
const pads = computed(() =>
  collageItems.value.length ? Math.max(0, 4 - collageItems.value.length) : 0
)

// 非视频文件（图片 / 其它）的占位图标
const fileIcon = computed(() => {
  if (kind.value === 'image') return '🖼'
  if (kind.value === 'video') return '🎞'
  return '📄'
})

const dateText = computed(() => fmtTime(props.entry.mtime))

// 扩展名徽章：图片和视频的缩略图一样，靠它一眼区分类型
const extText = computed(() => {
  const m = /\.([A-Za-z0-9]+)$/.exec(props.entry.name)
  return (m?.[1] ?? '').toUpperCase()
})
</script>

<template>
  <div class="card film-card" :class="{ folder: isFolder }">
    <div
      class="thumb-wrap"
      @click="isFolder ? emit('navigate', entry) : emit('preview', entry)"
    >
      <!-- 文件夹：统一「标签 + 主体」轮廓；主体内按子视频数量展示缩略图 / 拼贴 / 图标 -->
      <div v-if="isFolder" class="folder-shape">
        <div class="folder-tab"></div>

        <!-- 单视频文件夹：主体内放大缩略图 -->
        <div v-if="isSingleVideoFolder" class="folder-body single">
          <img
            v-if="showFolderThumb"
            :src="folderThumbUrl"
            alt=""
            loading="lazy"
            @error="folderThumbFailed = true"
          />
          <div v-else class="thumb-empty">🎞</div>
        </div>

        <!-- 多视频文件夹：2x2 拼贴 -->
        <div v-else-if="isMultiVideoFolder" class="folder-body collage">
          <img
            v-for="it in collageItems"
            :key="it.i"
            :src="it.url"
            alt=""
            loading="lazy"
            @error="failedCollage = [...failedCollage, it.i]"
          />
          <div v-for="i in pads" :key="'pad' + i" class="collage-pad"></div>
          <div v-if="!collageItems.length" class="thumb-empty">🗂</div>
        </div>

        <!-- 空文件夹 / 尚未扫描到的文件夹 -->
        <div v-else class="folder-body plain">
          <div class="thumb-empty">🗂</div>
        </div>

        <span v-if="isMultiVideoFolder" class="count-badge">{{ entry.video_count }}</span>
      </div>

      <!-- 文件：图片用自身作缩略图，其余显示文件图标 -->
      <template v-else>
        <img v-if="showThumb" :src="thumbUrl" alt="" loading="lazy" @error="thumbFailed = true" />
        <div v-else class="thumb-empty">{{ fileIcon }}</div>
      </template>

      <!-- 番号角标：悬浮显示在缩略图左上角 -->
      <span v-if="entry.code" class="code-badge">{{ entry.code }}</span>

      <!-- 扩展名徽章：右下角常驻（文件夹不显示，它们有数量角标） -->
      <span v-if="!isFolder && extText" class="ext-badge">{{ extText }}</span>
    </div>

    <div class="meta">
      <div class="name" :title="entry.name">{{ entry.name }}</div>
      <div class="sub muted small">{{ dateText }}</div>
      <div class="actions">
        <button
          v-if="!isFolder"
          class="btn btn-sm"
          :title="entry.local_path || '未配置路径映射'"
          @click="emit('open', entry)"
        >
          打开
        </button>
        <button class="btn btn-sm" @click="emit('rename', entry)">重命名</button>
        <button class="btn btn-sm danger" @click="emit('delete', entry)">删除</button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.film-card {
  display: flex;
  flex-direction: column;
  gap: 0;
  overflow: hidden;
}

.thumb-wrap {
  position: relative;
  aspect-ratio: 16 / 10;
  background: #f0f1f5;
  display: flex;
  align-items: center;
  justify-content: center;
  overflow: hidden;
  cursor: pointer;
}

.thumb-wrap > img {
  width: 100%;
  height: 100%;
  object-fit: cover;
}

.thumb-empty {
  font-size: 34px;
  opacity: 0.45;
  line-height: 1;
}

/* 文件夹轮廓：顶部标签 + 主体，缩略图占比要大 */
.folder-shape {
  position: relative;
  width: 92%;
  height: 88%;
  display: flex;
  flex-direction: column;
}

.folder-tab {
  height: 12%;
  min-height: 10px;
  width: 34%;
  background: #d9dce6;
  border-radius: 6px 8px 0 0;
}

.folder-body {
  flex: 1;
  min-height: 0;
  background: #e4e7ef;
  border-radius: 0 8px 8px 8px;
  padding: 4px;
  overflow: hidden;
  display: grid;
  place-items: center;
}

/* 空文件夹：图标居中 */
.folder-body.plain {
  grid-template-columns: 1fr;
  grid-template-rows: 1fr;
}

/* 单视频：一张图铺满主体 */
.folder-body.single {
  grid-template-columns: 1fr;
  grid-template-rows: 1fr;
  place-items: stretch;
}

/* 多视频：2x2 拼贴 */
.folder-body.collage {
  grid-template-columns: repeat(2, 1fr);
  grid-template-rows: repeat(2, 1fr);
  gap: 2px;
  place-items: stretch;
}

.folder-body img {
  width: 100%;
  height: 100%;
  object-fit: cover;
  border-radius: 4px;
}

.collage-pad {
  background: #eef0f5;
  border-radius: 4px;
}

/* 拼贴里没有任何图时，图标占满 2x2 并居中 */
.folder-body.collage .thumb-empty {
  grid-column: span 2;
  grid-row: span 2;
  place-self: center;
}

.count-badge {
  position: absolute;
  right: 8px;
  bottom: 8px;
  background: rgba(20, 24, 34, 0.72);
  color: #fff;
  font-size: 11px;
  font-weight: 600;
  padding: 2px 8px;
  border-radius: 10px;
}

.code-badge {
  position: absolute;
  left: 8px;
  top: 8px;
  background: var(--accent);
  color: #fff;
  font-family: var(--mono);
  font-size: 11px;
  font-weight: 600;
  padding: 2px 8px;
  border-radius: 6px;
  opacity: 0;
  transition: opacity 0.15s;
}

.film-card:hover .code-badge {
  opacity: 1;
}

/* 扩展名徽章：常驻右下角，区分图片/视频（缩略图一样时唯一的视觉线索） */
.ext-badge {
  position: absolute;
  right: 8px;
  bottom: 8px;
  background: rgba(20, 24, 34, 0.72);
  color: #fff;
  font-family: var(--mono);
  font-size: 10.5px;
  font-weight: 600;
  letter-spacing: 0.5px;
  padding: 2px 7px;
  border-radius: 9px;
  pointer-events: none;
}

.meta {
  padding: 10px 12px 12px;
  display: grid;
  gap: 4px;
}

.name {
  font-size: 13px;
  font-weight: 500;
  line-height: 1.35;
  /* 固定占 3 行：名字短时也不塌陷，同一排卡片的日期才能对齐 */
  height: calc(1.35em * 3);
  display: -webkit-box;
  -webkit-box-orient: vertical;
  -webkit-line-clamp: 3;
  overflow: hidden;
  word-break: break-all;
}

.sub {
  line-height: 1.2;
}

.actions {
  display: flex;
  gap: 6px;
  margin-top: 4px;
}

.actions .btn {
  font-size: 12px;
  padding: 3px 9px;
}

.actions .danger {
  color: var(--err);
}

/* 窄屏：没有 hover，番号角标常驻；按钮缩小让一行放得下 */
@media (max-width: 860px) {
  .meta {
    padding: 8px 9px 10px;
    gap: 3px;
  }

  .name {
    font-size: 12.5px;
    /* 窄屏两行足够，省出高度 */
    height: calc(1.35em * 2);
    -webkit-line-clamp: 2;
  }

  .code-badge {
    opacity: 1;
    font-size: 10px;
    padding: 2px 6px;
    left: 6px;
    top: 6px;
  }

  .ext-badge {
    right: 6px;
    bottom: 6px;
    padding: 2px 6px;
  }

  .count-badge {
    right: 6px;
    bottom: 6px;
  }

  .actions {
    gap: 4px;
  }

  .actions .btn {
    flex: 1;
    padding: 5px 4px;
    font-size: 11.5px;
  }
}
</style>
