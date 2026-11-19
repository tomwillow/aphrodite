<script setup lang="ts">
import { useRouter } from 'vue-router'

const router = useRouter()
</script>

<template>
  <section class="library-page">
    <div class="page-head">
      <h1>影片管理</h1>
      <p class="muted small">管理本地影片库：浏览、整理、补全信息。该功能正在规划中，敬请期待。</p>
    </div>

    <div class="card placeholder">
      <div class="placeholder-icon">🎬</div>
      <p class="placeholder-title">功能开发中</p>
      <p class="muted small">
        在此之前，可以先使用番号抓取功能，把影片的封面和详情图抓到统一图库里。
      </p>
      <router-link to="/capture" class="btn btn-primary btn-sm">去番号抓取</router-link>
    </div>
  </section>
</template>

<style scoped>
.library-page {
  max-width: 820px;
  margin: 0 auto;
}

.page-head {
  margin-bottom: 18px;
}

.placeholder {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 10px;
  padding: 56px 24px;
  text-align: center;
}

.placeholder-icon {
  font-size: 40px;
  line-height: 1;
  opacity: 0.85;
}

.placeholder-title {
  margin: 0;
  font-size: 15px;
  font-weight: 600;
  color: var(--text);
}

.placeholder .btn {
  margin-top: 8px;
}
</style>
