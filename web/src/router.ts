import { createRouter, createWebHistory } from 'vue-router'
import LibraryView from './views/LibraryView.vue'
import SubmitView from './views/SubmitView.vue'
import TaskListView from './views/TaskListView.vue'
import TaskDetailView from './views/TaskDetailView.vue'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', name: 'library', component: LibraryView },
    { path: '/capture', name: 'submit', component: SubmitView },
    { path: '/tasks', name: 'tasks', component: TaskListView },
    { path: '/tasks/:id', name: 'task', component: TaskDetailView, props: true },
    { path: '/:pathMatch(.*)*', redirect: '/' }
  ]
})

export default router
