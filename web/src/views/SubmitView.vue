<script setup lang="ts">
import { computed, ref } from 'vue'
import { useRouter } from 'vue-router'
import { parseText } from '../lib/codes'
import { createTask } from '../api'

const router = useRouter()
const text = ref('')
const submitting = ref(false)
const error = ref('')

const parsed = computed(() => parseText(text.value))
const canSubmit = computed(() => parsed.value.count > 0 && !submitting.value)

const manyCodes = computed(() => parsed.value.count > 100)
const hasProblems = computed(
  () =>
    parsed.value.noLink.length > 0 ||
    parsed.value.onlyLink.length > 0 ||
    parsed.value.unknown.length > 0
)

async function submit() {
  if (!canSubmit.value) return
  submitting.value = true
  error.value = ''
  try {
    const snap = await createTask(text.value)
    router.push(`/tasks/${snap.task.id}`)
  } catch (e) {
    error.value = e instanceof Error ? e.message : '提交失败'
  } finally {
    submitting.value = false
  }
}

function clearAll() {
  text.value = ''
  error.value = ''
}

const sample =
  'ed2k://|file|@IPX-528_restored.mp4|10174562190|12002AEC19A55208154D2683A8585483|/'
</script>

<template>
  <section class="submit-page">
    <div class="page-head">
      <h1>新建抓取任务</h1>
      <p class="muted small">
        粘贴任意文本（通常一行一个条目）。系统会识别其中形如 <code>XXX-XXX</code> 的番号，
        并把同一行的下载链接一并存到条目里，再去 javbus 抓取主图和详情图。
      </p>
    </div>

    <div class="card editor">
      <textarea
        v-model="text"
        rows="8"
        spellcheck="false"
        placeholder="每行一个条目，例如：
ed2k://|file|@IPX-528_restored.mp4|10174562190|12002AEC…|/"
        @keydown.ctrl.enter.prevent="submit"
        @keydown.meta.enter.prevent="submit"
      ></textarea>

      <div class="bar">
        <div class="counter">
          <span class="num" :class="{ zero: parsed.withLink === 0 }">{{ parsed.withLink }}</span>
          <span class="muted small">个带链接的番号</span>
          <template v-if="parsed.count !== parsed.withLink">
            <span class="muted small sep">·</span>
            <span class="muted small">共 {{ parsed.count }} 个番号</span>
          </template>
        </div>
        <div class="bar-actions">
          <button v-if="text" class="btn btn-sm ghost" @click="clearAll">清空</button>
          <button class="btn btn-primary" :disabled="!canSubmit" @click="submit">
            {{ submitting ? '提交中…' : `提交任务${parsed.count ? '（' + parsed.count + '）' : ''}` }}
          </button>
        </div>
      </div>

      <p v-if="error" class="err">{{ error }}</p>
      <p v-else-if="manyCodes" class="hint warn-hint">
        番号较多，抓取需要较长时间。提交后可离开本页，在任务列表中随时查看进度。
      </p>
      <p v-else class="hint">
        提示：Ctrl / ⌘ + Enter 可直接提交。示例 ——
        <button class="link" @click="text = sample">{{ sample }}</button>
      </p>
    </div>

    <div v-if="hasProblems" class="problems">
      <p class="problems-head muted small">
        以下行没有完整识别，请核对；它们不会因分类而被丢弃——有番号的行仍会照常抓取。
      </p>

      <section v-if="parsed.noLink.length" class="problem-card warn">
        <header>
          <span class="dot"></span>
          <strong>缺少下载链接</strong>
          <span class="muted small">{{ parsed.noLink.length }} 行</span>
        </header>
        <ul>
          <li v-for="(row, i) in parsed.noLink" :key="'nl' + i">
            <span class="code-tag">{{ row.code }}</span>
            <span class="line">{{ row.line }}</span>
          </li>
        </ul>
      </section>

      <section v-if="parsed.onlyLink.length" class="problem-card warn">
        <header>
          <span class="dot"></span>
          <strong>只有链接，没有番号</strong>
          <span class="muted small">{{ parsed.onlyLink.length }} 行</span>
        </header>
        <ul>
          <li v-for="(row, i) in parsed.onlyLink" :key="'ol' + i">
            <span class="line mono">{{ row.link }}</span>
          </li>
        </ul>
      </section>

      <section v-if="parsed.unknown.length" class="problem-card">
        <header>
          <span class="dot"></span>
          <strong>未识别出番号</strong>
          <span class="muted small">{{ parsed.unknown.length }} 行</span>
        </header>
        <ul>
          <li v-for="(row, i) in parsed.unknown" :key="'uk' + i">
            <span class="line">{{ row.line }}</span>
          </li>
        </ul>
      </section>
    </div>
  </section>
</template>

<style scoped>
.submit-page {
  max-width: 820px;
  margin: 0 auto;
}

.page-head {
  margin-bottom: 18px;
}

.page-head code {
  font-family: var(--mono);
  font-size: 12.5px;
  background: #eceef4;
  border-radius: 5px;
  padding: 1px 5px;
}

.editor {
  padding: 0;
  overflow: hidden;
}

textarea {
  display: block;
  width: 100%;
  border: 0;
  outline: none;
  resize: vertical;
  min-height: 176px;
  padding: 18px 20px;
  font-family: var(--mono);
  font-size: 13px;
  line-height: 1.85;
  color: var(--text);
  background: #fcfcfd;
}

textarea::placeholder {
  color: #b3b8c4;
}

.bar {
  display: flex;
  align-items: center;
  gap: 16px;
  padding: 12px 16px;
  border-top: 1px solid var(--line);
  background: #fff;
}

.counter {
  display: flex;
  align-items: baseline;
  gap: 6px;
}

.counter .num {
  font-size: 22px;
  font-weight: 650;
  font-variant-numeric: tabular-nums;
  color: var(--accent);
  line-height: 1;
}

.counter .num.zero {
  color: var(--faint);
}

.counter .sep {
  opacity: 0.5;
}

.bar-actions {
  margin-left: auto;
  display: flex;
  align-items: center;
  gap: 8px;
}

.ghost {
  border-color: transparent;
  color: var(--muted);
  background: transparent;
}

.hint,
.err {
  margin: 0;
  padding: 0 16px 16px;
  font-size: 12.5px;
  color: var(--faint);
}

.warn-hint {
  color: var(--warn);
}

.err {
  color: var(--err);
}

.link {
  border: 0;
  background: none;
  padding: 0;
  color: var(--accent);
  font-family: var(--mono);
  font-size: 11.5px;
  text-decoration: underline dotted;
  text-align: left;
  word-break: break-all;
}

.problems {
  margin-top: 18px;
  display: grid;
  gap: 12px;
}

.problems-head {
  margin: 0;
}

.problem-card {
  background: var(--panel);
  border: 1px solid var(--line);
  border-radius: var(--radius);
  box-shadow: var(--shadow);
  overflow: hidden;
}

.problem-card header {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 11px 16px;
  border-bottom: 1px solid var(--line);
  font-size: 13px;
}

.problem-card .dot {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: #b9bfcc;
  flex: 0 0 auto;
}

.problem-card.warn .dot {
  background: var(--warn);
}

.problem-card.warn header strong {
  color: #8a6417;
}

.problem-card ul {
  margin: 0;
  padding: 4px 0;
  list-style: none;
  max-height: 210px;
  overflow-y: auto;
}

.problem-card li {
  display: flex;
  align-items: baseline;
  gap: 8px;
  padding: 6px 16px;
  font-size: 12px;
  line-height: 1.6;
}

.problem-card li:hover {
  background: #fafbfc;
}

.code-tag {
  flex: 0 0 auto;
  font-family: var(--mono);
  font-size: 11.5px;
  padding: 1px 6px;
  border-radius: 5px;
  background: var(--accent-soft);
  color: var(--accent);
}

.line {
  min-width: 0;
  word-break: break-all;
  color: var(--muted);
}

.line.mono {
  font-family: var(--mono);
  font-size: 11.5px;
}

/* ---------------------------------------------------------------- 窄屏 */
@media (max-width: 760px) {
  textarea {
    padding: 12px 14px;
    font-size: 12.5px;
    min-height: 150px;
  }

  .bar {
    flex-wrap: wrap;
    gap: 10px;
    padding: 10px 14px;
  }

  .bar-actions {
    margin-left: 0;
    width: 100%;
    justify-content: flex-end;
  }

  .problem-card li {
    padding: 6px 12px;
  }
}
</style>
