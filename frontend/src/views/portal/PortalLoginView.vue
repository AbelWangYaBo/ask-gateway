<template>
  <div class="login-wrap">
    <el-card class="login-card">
      <h2>ask-gateway 用户中心</h2>
      <p class="hint">使用你的 API Key（sk-ask- 开头）登录，查看自己的请求与消耗</p>
      <el-form @submit.prevent="login">
        <el-form-item>
          <el-input
            v-model="apiKey"
            type="password"
            show-password
            placeholder="sk-ask-..."
            size="large"
            @keyup.enter="login"
          />
        </el-form-item>
        <el-button
          type="primary"
          size="large"
          style="width: 100%"
          :loading="loading"
          @click="login"
        >
          登录
        </el-button>
      </el-form>
    </el-card>
  </div>
</template>

<script setup lang="ts">
import { ref } from 'vue'
import { ElMessage } from 'element-plus'
import { useRouter } from 'vue-router'
import { portalApi } from '@/api'
import { ApiError } from '@/api/client'
import { PORTAL_KEY_STORAGE } from '@/api/client'

const router = useRouter()
const apiKey = ref('')
const loading = ref(false)

async function login() {
  const key = apiKey.value.trim()
  if (!key) {
    ElMessage.warning('请输入 API Key')
    return
  }
  loading.value = true
  try {
    await portalApi.login(key)
    localStorage.setItem(PORTAL_KEY_STORAGE, key)
    void router.push('/portal/dashboard')
  } catch (error) {
    ElMessage.error(
      error instanceof ApiError && error.status === 401
        ? 'API Key 无效、已停用或已到期'
        : '登录失败',
    )
  } finally {
    loading.value = false
  }
}
</script>

<style scoped>
.login-wrap {
  display: flex;
  align-items: center;
  justify-content: center;
  min-height: 100vh;
  background: #f3f4f6;
}
.login-card {
  width: 380px;
}
.hint {
  color: #6b7280;
  font-size: 13px;
  margin: 8px 0 20px;
}
</style>
