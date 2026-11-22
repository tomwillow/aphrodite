<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'

export interface LightboxImage {
  src: string
  caption: string
}

const props = withDefaults(
  defineProps<{
    images: LightboxImage[]
    /** 初始定位到的图片下标 */
    start?: number
  }>(),
  { start: 0 }
)
const emit = defineEmits<{ (e: 'close'): void }>()

const viewportRef = ref<HTMLElement | null>(null)
const idx = ref(Math.min(Math.max(props.start, 0), Math.max(props.images.length - 1, 0)))
const cur = computed(() => props.images[idx.value])
const dimensions = ref('')
// false = 适配屏幕；true = 1:1 原始大小（拖拽平移）
const zoomed = ref(false)

const toast = ref('')
let toastTimer: number | undefined
function showToast(msg: string) {
  toast.value = msg
  window.clearTimeout(toastTimer)
  toastTimer = window.setTimeout(() => (toast.value = ''), 1500)
}

function go(dir: 1 | -1) {
  const next = idx.value + dir
  if (next < 0) {
    showToast('已经是第一张')
    return
  }
  if (next >= props.images.length) {
    showToast('已经是最后一张')
    return
  }
  idx.value = next
  zoomed.value = false
  const vp = viewportRef.value
  if (vp) vp.scrollTo(0, 0)
}

// 滚轮翻页：一次滚动手势只翻一张（锁 260ms 防触控板连滚）
let wheelLockUntil = 0
function onWheel(e: WheelEvent) {
  const now = Date.now()
  if (now < wheelLockUntil) return
  wheelLockUntil = now + 260
  e.preventDefault()
  go(e.deltaY > 0 ? 1 : -1)
}

function onKey(e: KeyboardEvent) {
  if (e.key === 'Escape') emit('close')
  else if (e.key === 'ArrowLeft') go(-1)
  else if (e.key === 'ArrowRight') go(1)
}

function onImgLoad(e: Event) {
  const img = e.target as HTMLImageElement
  dimensions.value = `${img.naturalWidth} × ${img.naturalHeight}px`
}

// 点击图片在 适配屏幕 / 1:1 之间切换；1:1 时拖拽平移
let suppressClick = false
function toggleZoom() {
  if (suppressClick) {
    suppressClick = false
    return
  }
  zoomed.value = !zoomed.value
  const vp = viewportRef.value
  if (vp && !zoomed.value) vp.scrollTo(0, 0)
}

let dragging = false
let dragMoved = false
let sx = 0
let sy = 0
let sl = 0
let st = 0
function startDrag(e: MouseEvent) {
  if (!zoomed.value) return
  dragging = true
  dragMoved = false
  sx = e.clientX
  sy = e.clientY
  const vp = viewportRef.value
  if (vp) {
    sl = vp.scrollLeft
    st = vp.scrollTop
  }
  window.addEventListener('mousemove', onDrag)
  window.addEventListener('mouseup', endDrag)
}
function onDrag(e: MouseEvent) {
  if (!dragging) return
  const dx = e.clientX - sx
  const dy = e.clientY - sy
  if (Math.abs(dx) > 4 || Math.abs(dy) > 4) dragMoved = true
  const vp = viewportRef.value
  if (vp) {
    vp.scrollLeft = sl - dx
    vp.scrollTop = st - dy
  }
}
function endDrag() {
  dragging = false
  suppressClick = dragMoved
  window.removeEventListener('mousemove', onDrag)
  window.removeEventListener('mouseup', endDrag)
}

onMounted(() => {
  window.addEventListener('keydown', onKey)
  document.body.style.overflow = 'hidden'
})

onBeforeUnmount(() => {
  window.removeEventListener('keydown', onKey)
  document.body.style.overflow = ''
  window.removeEventListener('mousemove', onDrag)
  window.removeEventListener('mouseup', endDrag)
  window.clearTimeout(toastTimer)
})
</script>

<template>
  <teleport to="body">
    <div class="lightbox">
      <div
        ref="viewportRef"
        class="viewport"
        :class="{ zoomed }"
        @click.self="emit('close')"
        @wheel="onWheel"
      >
        <img
          :key="cur.src"
          :src="cur.src"
          :alt="cur.caption"
          :class="{ zoomed }"
          draggable="false"
          @click="toggleZoom"
          @mousedown.prevent="startDrag"
          @load="onImgLoad"
        />
      </div>

      <button class="nav prev" title="上一张（←）" @click="go(-1)">‹</button>
      <button class="nav next" title="下一张（→）" @click="go(1)">›</button>

      <div class="hud">
        <span class="caption" :title="cur.caption">{{ cur.caption }}</span>
        <span v-if="images.length > 1" class="counter">{{ idx + 1 }} / {{ images.length }}</span>
        <span v-if="dimensions" class="dims">{{ dimensions }}</span>
        <span class="tip">滚轮 / ←→ 翻页 · 点图 1:1 · Esc 关闭</span>
      </div>
      <button class="close" title="关闭" @click="emit('close')">×</button>

      <transition name="toast">
        <div v-if="toast" class="toast">{{ toast }}</div>
      </transition>
    </div>
  </teleport>
</template>

<style scoped>
.lightbox {
  position: fixed;
  inset: 0;
  z-index: 1000;
  background: rgba(10, 11, 16, 0.94);
  backdrop-filter: blur(2px);
}

.viewport {
  position: absolute;
  inset: 0;
  overflow: hidden;
  padding: 64px 96px 56px;
  display: flex;
  align-items: center;
  justify-content: center;
}

.viewport.zoomed {
  overflow: auto;
  padding: 24px;
  align-items: flex-start;
  justify-content: flex-start;
}

.viewport img {
  display: block;
  max-width: 100%;
  max-height: 100%;
  object-fit: contain;
  border-radius: 4px;
  box-shadow: 0 24px 80px rgba(0, 0, 0, 0.6);
  cursor: zoom-in;
  user-select: none;
}

.viewport img.zoomed {
  max-width: none;
  max-height: none;
  cursor: grab;
}

.nav {
  position: fixed;
  top: 50%;
  transform: translateY(-50%);
  width: 44px;
  height: 44px;
  border-radius: 50%;
  border: 1px solid rgba(255, 255, 255, 0.22);
  background: rgba(255, 255, 255, 0.1);
  color: #fff;
  font-size: 26px;
  line-height: 1;
  display: flex;
  align-items: center;
  justify-content: center;
  transition: background 0.15s, transform 0.15s;
}

.nav:hover {
  background: rgba(255, 255, 255, 0.22);
  transform: translateY(-50%) scale(1.06);
}

.nav.prev {
  left: 18px;
}

.nav.next {
  right: 18px;
}

.hud {
  position: fixed;
  left: 24px;
  bottom: 18px;
  display: flex;
  align-items: center;
  gap: 14px;
  max-width: calc(100vw - 130px);
  color: rgba(255, 255, 255, 0.62);
  font-size: 12px;
  pointer-events: none;
}

.caption {
  color: rgba(255, 255, 255, 0.9);
  font-family: var(--mono);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  max-width: 46vw;
}

.counter {
  font-variant-numeric: tabular-nums;
  color: rgba(255, 255, 255, 0.85);
}

.dims {
  font-variant-numeric: tabular-nums;
}

.tip {
  opacity: 0.7;
}

.close {
  position: fixed;
  top: 16px;
  right: 18px;
  width: 40px;
  height: 40px;
  border-radius: 50%;
  border: 1px solid rgba(255, 255, 255, 0.22);
  background: rgba(255, 255, 255, 0.1);
  color: #fff;
  font-size: 22px;
  line-height: 1;
  display: flex;
  align-items: center;
  justify-content: center;
  transition: background 0.15s, transform 0.15s;
}

.close:hover {
  background: rgba(255, 255, 255, 0.22);
  transform: scale(1.05);
}

.toast {
  position: fixed;
  top: 22px;
  left: 50%;
  transform: translateX(-50%);
  background: rgba(30, 34, 46, 0.92);
  border: 1px solid rgba(255, 255, 255, 0.18);
  color: rgba(255, 255, 255, 0.92);
  font-size: 13px;
  padding: 8px 18px;
  border-radius: 18px;
  z-index: 1001;
}

.toast-enter-active,
.toast-leave-active {
  transition: opacity 0.18s;
}

.toast-enter-from,
.toast-leave-to {
  opacity: 0;
}

/* ---------------------------------------------------------------- 窄屏 */
@media (max-width: 760px) {
  .viewport {
    padding: 56px 8px 48px;
  }

  .nav {
    width: 38px;
    height: 38px;
    font-size: 22px;
    background: rgba(255, 255, 255, 0.16);
  }

  .nav.prev {
    left: 8px;
  }

  .nav.next {
    right: 8px;
  }

  .close {
    top: 10px;
    right: 10px;
  }

  .hud {
    left: 14px;
    right: 14px;
    bottom: calc(12px + env(safe-area-inset-bottom));
    gap: 10px;
    max-width: none;
  }

  .caption {
    max-width: 52vw;
  }

  /* 触摸设备没有滚轮 / 方向键，隐藏键盘提示 */
  .tip,
  .dims {
    display: none;
  }
}
</style>
