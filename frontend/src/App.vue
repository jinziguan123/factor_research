<script setup lang="ts">
import {
  NConfigProvider,
  NLayout, NLayoutSider, NLayoutContent, NLayoutHeader,
  NMessageProvider, NDialogProvider, NNotificationProvider, NLoadingBarProvider,
} from 'naive-ui'
import { binanceThemeOverrides } from '@/styles/theme'
import AppSidebar from '@/components/layout/AppSidebar.vue'
import AppHeader from '@/components/layout/AppHeader.vue'
</script>

<template>
  <n-config-provider :theme-overrides="binanceThemeOverrides">
    <n-loading-bar-provider>
      <n-dialog-provider>
        <n-notification-provider>
          <n-message-provider>
            <n-layout has-sider class="app-root">
              <n-layout-sider :width="220" bordered content-style="padding: 0">
                <app-sidebar />
              </n-layout-sider>
              <n-layout>
                <n-layout-header bordered style="padding: 0 20px; height: 56px; line-height: 56px">
                  <app-header />
                </n-layout-header>
                <n-layout-content content-style="padding: 20px" class="app-main">
                  <router-view />
                </n-layout-content>
              </n-layout>
            </n-layout>
          </n-message-provider>
        </n-notification-provider>
      </n-dialog-provider>
    </n-loading-bar-provider>
  </n-config-provider>
</template>

<style>
html, body { margin: 0; padding: 0; height: 100%; }
.app-root { height: 100vh; }
.app-main { background: #FAFAFA; min-height: calc(100vh - 56px); }
</style>
