<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { startOrganize, startThumbs, type FilmDir } from '../api'

const props = defineProps<{
  open: boolean
  /** organize | thumb */
  tool: 'organize' | 'thumb'
  dirs: FilmDir[]
  currentDirId: number | null
  /** 当前浏览的目录相对路径（scope=current 时从这里开始） */
  currentPath: string
}>()

const emit = defineEmits<{
  (e: 'close'): void
  (e: 'started', taskId: number, tool: 'organize' | 'thumb'): void
}>()

const scope = ref<'all' | 'current'>('all')
const recursive = ref(true)
const busy = ref(false)
const error = ref('')

const title = computed(() =>
  props.tool === 'organize' ? '整理番号目录' : '补全缩略图'
)

watch(
  () => props.open,
  (v) => {
    if (v) {
      scope.value = 'all'
      recursive.value = true
      error.value = ''
      busy.value = false
    }
  }
)

async function submit() {
  busy.value = true
  error.value = ''
  try {
    const fn = props.tool === 'organize' ? startOrganize : startThumbs
    const taskId = await fn({
      scope: scope.value,
      dirId: scope.value === 'current' ? props.currentDirId : null,
      path: scope.value === 'current' ? props.currentPath : '',
      recursive: recursive.value
    })
    emit('started', taskId, props.tool)
    emit('close')
  } catch (e) {
    error.value = e instanceof Error ? e.message : '启动失败'
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <div v-if="open" class="dlg-mask" @click.self="emit('close')">
    <div class="dlg">
      <h3>{{ title }}</h3>
      <p class="muted small">
        {{
          tool === 'organize'
            ? '识别出番号的视频移动到 [番号][演员][标题] 目录，图片优先从图库复制'
            : 'ffmpeg 为无缩略图影片抽帧生成同名 jpg（识别出番号的影片不走此任务）'
        }}
      </p>

      <div class="field-label">处理范围</div>
      <label class="opt"><input v-model="scope" type="radio" value="all" /> 全库（所有影片目录）</label>
      <label class="opt">
        <input v-model="scope" type="radio" value="current" />
        当前目录<span v-if="currentDirId !== null">（{{ currentPath || '根级' }}）</span>
      </label>
      <label v-if="scope === 'current'" class="opt indent">
        <input v-model="recursive" type="checkbox" /> 包含子目录（遍历）
      </label>

      <div class="rules muted small">
        <template v-if="tool === 'organize'">
          · 目录已是标准命名（[番号] 开头且含数字）→ 跳过<br />
          · 演员超过 3 人只取前 3 人加“...”<br />
          · 详情图全量归集，图库未命中则联网抓<br />
          · 不做预扫：启动后任务条目动态增加，实时显示总数
        </template>
        <template v-else>
          · 已有同名缩略图 → 跳过<br />
          · 识别出番号的影片不走此任务（归整理任务）<br />
          · 取视频时长中点附近帧，输出 文件名.jpg<br />
          · 抽帧逐个串行进行，避免 USB 盘卡死
        </template>
      </div>

      <p v-if="error" class="err small">{{ error }}</p>

      <div class="dlg-actions">
        <button class="btn btn-sm" :disabled="busy" @click="emit('close')">取消</button>
        <button class="btn btn-primary btn-sm" :disabled="busy" @click="submit">
          {{ busy ? '启动中…' : '启动任务' }}
        </button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.dlg-mask {
  position: fixed;
  inset: 0;
  background: rgba(15, 23, 42, 0.45);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 60;
}

.dlg {
  width: min(480px, calc(100vw - 32px));
  background: var(--card, #fff);
  border: 1px solid var(--line, #e5e7eb);
  border-radius: 14px;
  padding: 20px 22px;
}

.dlg h3 {
  margin: 0 0 6px;
  font-size: 16px;
}

.field-label {
  font-size: 13px;
  font-weight: 650;
  margin: 14px 0 4px;
}

.opt {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 13.5px;
  padding: 5px 0;
  cursor: pointer;
}

.opt.indent {
  margin-left: 22px;
}

.rules {
  border-top: 1px solid var(--line);
  margin-top: 12px;
  padding-top: 10px;
  line-height: 1.8;
}

.err {
  color: var(--err);
  margin-top: 10px;
}

.dlg-actions {
  display: flex;
  justify-content: flex-end;
  gap: 10px;
  margin-top: 16px;
}
</style>
