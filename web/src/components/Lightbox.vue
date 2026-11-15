<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from 'vue'

defineProps<{ src: string; caption?: string }>()
const emit = defineEmits<{ (e: 'close'): void }>()

const dimensions = ref('')

function onKey(e: KeyboardEvent) {
  if (e.key === 'Escape') emit('close')
}

function onImgLoad(e: Event) {
  const img = e.target as HTMLImageElement
  dimensions.value = `${img.naturalWidth} × ${img.naturalHeight}px`
}

onMounted(() => {
  window.addEventListener('keydown', onKey)
  document.body.style.overflow = 'hidden'
})

onBeforeUnmount(() => {
  window.removeEventListener('keydown', onKey)
  document.body.style.overflow = ''
})
</script>

<template>
  <teleport to="body">
    <div class="lightbox">
      <div class="viewport" @click.self="emit('close')">
        <img :src="src" :alt="caption || ''" @click="emit('close')" @load="onImgLoad" />
      </div>
      <div class="hud">
        <span v-if="caption" class="caption">{{ caption }}</span>
        <span v-if="dimensions" class="dims">{{ dimensions }}</span>
        <span class="tip">点击图片或按 Esc 关闭</span>
      </div>
      <button class="close" title="关闭" @click="emit('close')">×</button>
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
  overflow: auto;
  padding: 64px 40px 56px;
}

.viewport img {
  display: block;
  margin: 0 auto;
  max-width: none;
  max-height: none;
  cursor: zoom-out;
  border-radius: 4px;
  box-shadow: 0 24px 80px rgba(0, 0, 0, 0.6);
}

.hud {
  position: fixed;
  left: 24px;
  bottom: 18px;
  display: flex;
  align-items: center;
  gap: 14px;
  color: rgba(255, 255, 255, 0.62);
  font-size: 12px;
  pointer-events: none;
}

.caption {
  color: rgba(255, 255, 255, 0.9);
  font-family: var(--mono);
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
</style>
