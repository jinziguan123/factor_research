import { createApp } from 'vue'
import { createPinia } from 'pinia'
import { createRouter, createWebHistory } from 'vue-router'
import { VueQueryPlugin } from '@tanstack/vue-query'
import App from './App.vue'
import { routes } from './router'
import './styles/global.scss'

const router = createRouter({ history: createWebHistory(), routes })

createApp(App)
  .use(createPinia())
  .use(router)
  .use(VueQueryPlugin)
  .mount('#app')
