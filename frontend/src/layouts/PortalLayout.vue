<template>
  <el-container class="portal-layout">
    <el-aside width="200px" class="aside">
      <div class="brand">ask-gateway</div>
      <el-menu router :default-active="route.path" class="menu">
        <el-menu-item index="/portal/dashboard">
          <el-icon><DataBoard /></el-icon>我的概览
        </el-menu-item>
        <el-menu-item index="/portal/chat">
          <el-icon><ChatDotRound /></el-icon>对话
        </el-menu-item>
        <el-menu-item index="/portal/requests">
          <el-icon><Search /></el-icon>请求记录
        </el-menu-item>
        <el-menu-item index="/portal/usage">
          <el-icon><Coin /></el-icon>消耗统计
        </el-menu-item>
      </el-menu>
      <div class="footer">
        <el-button link @click="logout">退出登录</el-button>
      </div>
    </el-aside>
    <el-main class="main">
      <router-view />
    </el-main>
  </el-container>
</template>

<script setup lang="ts">
import { useRoute, useRouter } from 'vue-router'
import { clearPortalKey } from '@/api/client'

const route = useRoute()
const router = useRouter()

function logout() {
  clearPortalKey()
  void router.push('/portal/login')
}
</script>

<style scoped>
.portal-layout {
  min-height: 100vh;
}
.aside {
  display: flex;
  flex-direction: column;
  border-right: 1px solid #e5e7eb;
  background: #fff;
}
.brand {
  font-weight: 700;
  font-size: 16px;
  padding: 20px 16px 12px;
}
.menu {
  border-right: none;
  flex: 1;
}
.footer {
  padding: 16px;
  border-top: 1px solid #e5e7eb;
}
.main {
  background: #f3f4f6;
}
</style>
