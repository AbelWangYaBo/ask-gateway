<script setup lang="ts">
import { useRouter } from 'vue-router'

import { useAuthStore } from '@/stores/auth'

const router = useRouter()
const auth = useAuthStore()

const menus = [
  { index: '/dashboard', title: '看板', icon: 'DataBoard' },
  { index: '/keys', title: 'API Key 管理', icon: 'Key' },
  { index: '/requests', title: '审计检索', icon: 'Search' },
  { index: '/usage', title: '费用看板', icon: 'Coin' },
  { index: '/pricing', title: '定价规则', icon: 'PriceTag' },
  { index: '/audit-logs', title: '管理操作日志', icon: 'Document' },
]

function handleLogout() {
  auth.logout()
  router.replace({ name: 'login' })
}
</script>

<template>
  <el-container class="layout">
    <el-aside width="220px" class="aside">
      <div class="brand">ask-gateway</div>
      <el-menu :default-active="$route.path" router class="menu">
        <el-menu-item v-for="menu in menus" :key="menu.index" :index="menu.index">
          <el-icon><component :is="menu.icon" /></el-icon>
          <span>{{ menu.title }}</span>
        </el-menu-item>
      </el-menu>
    </el-aside>
    <el-container>
      <el-header class="header">
        <span class="page-title">{{ $route.meta.title ?? '' }}</span>
        <el-button text type="danger" @click="handleLogout">退出登录</el-button>
      </el-header>
      <el-main class="main">
        <router-view />
      </el-main>
    </el-container>
  </el-container>
</template>

<style scoped>
.layout {
  height: 100vh;
}
.aside {
  background: #1f2937;
  color: #fff;
}
.brand {
  height: 60px;
  line-height: 60px;
  text-align: center;
  font-size: 18px;
  font-weight: 600;
  letter-spacing: 0.5px;
}
.menu {
  border-right: none;
  background: transparent;
}
.menu :deep(.el-menu-item) {
  color: #d1d5db;
}
.menu :deep(.el-menu-item.is-active) {
  color: #fff;
  background: #2563eb;
}
.header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  border-bottom: 1px solid #e5e7eb;
  background: #fff;
}
.page-title {
  font-size: 16px;
  font-weight: 600;
}
.main {
  background: #f3f4f6;
  padding: 20px;
}
</style>
