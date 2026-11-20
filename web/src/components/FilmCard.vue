<script setup lang="ts">
import { computed } from 'vue'
import { filmMediaUrl, type FilmEntry } from '../api'
import { fmtTime } from '../lib/status'

const props = defineProps<{
  entry: FilmEntry
  dirId: number
}>()

const emit = defineEmits<{
  (e: 'navigate', entry: FilmEntry): void
  (e: 'open', entry: FilmEntry): void
  (e: 'rename', entry: FilmEntry): void
  (e: 'delete', entry: FilmEntry): void
}>()

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

const dateText = computed(() => fmtTime(props.entry.mtime))
</script>

<template>
  <div class="card film-card" :class="{ folder: isFolder }">
    <div class="thumb-wrap" @click="isFolder ? emit('navigate', entry) : undefined">
      <!-- 单视频文件夹：文件夹轮廓内嵌大缩略图 -->
      <template v-if="isSingleVideoFolder">
        <div class="folder-shape single">
          <div class="folder-tab"></div>
          <div class="folder-body single">
            <img v-if="folderThumbUrl" :src="folderThumbUrl" alt="" loading="lazy" />
            <div v-else class="thumb-empty">🎞</div>
          </div>
        </div>
      </template>

      <!-- 多视频文件夹：文件夹轮廓内嵌 2x2 拼贴 + 数量角标 -->
      <template v-else-if="isMultiVideoFolder">
        <div class="folder-shape single">
          <div class="folder-tab"></div>
          <div class="folder-body collage">
            <template v-if="collageUrls.length">
              <img
                v-for="(u, i) in collageUrls"
                :key="i"
                :src="u"
                alt=""
                loading="lazy"
              />
              <div
                v-for="i in Math.max(0, 4 - collageUrls.length)"
                :key="'pad' + i"
                class="collage-pad"
              ></div>
            </template>
            <div v-else class="thumb-empty">🗂</div>
          </div>
          <span class="count-badge">{{ entry.video_count }}</span>
        </div>
      </template>

      <!-- 空文件夹 -->
      <template v-else-if="isFolder">
        <div class="folder-shape empty">
          <div class="folder-tab"></div>
          <div class="folder-body">
            <div class="thumb-empty">🗂</div>
          </div>
        </div>
      </template>

      <!-- 视频文件 -->
      <template v-else>
        <img v-if="thumbUrl" :src="thumbUrl" alt="" loading="lazy" />
        <div v-else class="thumb-empty">🎞</div>
      </template>

      <!-- 番号角标：悬浮显示在缩略图左上角 -->
      <span v-if="entry.code" class="code-badge">{{ entry.code }}</span>
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

.folder .thumb-wrap {
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
  width: 34%;
  background: #d9dce6;
  border-radius: 6px 8px 0 0;
}

.folder-body {
  flex: 1;
  background: #e4e7ef;
  border-radius: 0 8px 8px 8px;
  display: grid;
  grid-template-columns: repeat(2, 1fr);
  grid-template-rows: repeat(2, 1fr);
  gap: 2px;
  padding: 4px;
  overflow: hidden;
}

.folder-body.single {
  grid-template-columns: 1fr;
  grid-template-rows: 1fr;
}

.folder-shape.single .folder-body {
  padding: 4px;
}

.folder-body img {
  width: 100%;
  height: 100%;
  object-fit: cover;
  border-radius: 4px;
  grid-column: span 2;
  grid-row: span 2;
}

.folder-body.collage img {
  grid-column: auto;
  grid-row: auto;
}

.collage-pad {
  background: #eef0f5;
  border-radius: 4px;
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

.meta {
  padding: 10px 12px 12px;
  display: grid;
  gap: 4px;
}

.name {
  font-size: 13px;
  font-weight: 500;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
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
</style>
