<script setup lang="ts"></script>

<template>
  <header class="topbar">
    <div class="wrap">
      <router-link to="/" class="brand"><span class="dot"></span>Aphrodite</router-link>
      <nav>
        <router-link to="/">新建任务</router-link>
        <router-link to="/tasks">任务列表</router-link>
      </nav>
    </div>
  </header>
  <main class="wrap main">
    <router-view />
  </main>
</template>
