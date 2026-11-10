<template>
  <el-dialog
    :model-value="visible"
    title="Key 验证"
    width="520px"
    @update:model-value="(v: boolean) => emit('update:visible', v)"
  >
    <el-form label-position="top" @submit.prevent>
      <el-form-item label="粘贴任意 Key 明文（平台 sk-ask- 或上游 DeepSeek Key）">
        <el-input
          v-model="plaintext"
          type="password"
          show-password
          placeholder="sk-..."
          @keyup.enter="verify"
        />
      </el-form-item>
    </el-form>

    <el-descriptions v-if="result" :column="1" border size="small" class="result">
      <el-descriptions-item label="判定">
        <el-tag :type="kindTag">{{ kindText }}</el-tag>
      </el-descriptions-item>
      <template v-if="result.kind !== 'unknown'">
        <el-descriptions-item label="标签">{{ result.label }}</el-descriptions-item>
        <el-descriptions-item v-if="result.holder_name" label="持有人">
          {{ result.holder_name }}
        </el-descriptions-item>
        <el-descriptions-item label="指纹/前缀">
          <code>{{ result.preview }}</code>
        </el-descriptions-item>
        <el-descriptions-item label="状态">{{ statusText }}</el-descriptions-item>
        <el-descriptions-item v-if="result.expires_at" label="到期时间">
          {{ formatDateTime(result.expires_at) }}
        </el-descriptions-item>
      </template>
    </el-descriptions>

    <template #footer>
      <el-button @click="emit('update:visible', false)">关闭</el-button>
      <el-button type="primary" :loading="loading" :disabled="!plaintext.trim()" @click="verify">
        验证
      </el-button>
    </template>
  </el-dialog>
</template>

<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { keysApi } from '@/api'
import type { KeyVerifyResult } from '@/api/types'
import { ApiError } from '@/api/client'
import { formatDateTime } from '@/utils/format'

const visible = defineModel<boolean>('visible', { default: false })
const emit = defineEmits<{ (e: 'update:visible', v: boolean): void }>()

const plaintext = ref('')
const loading = ref(false)
const result = ref<KeyVerifyResult | null>(null)

watch(visible, (v) => {
  if (v) {
    plaintext.value = ''
    result.value = null
  }
})

const kindText = computed(() =>
  result.value?.kind === 'platform'
    ? '本平台签发的 Key'
    : result.value?.kind === 'upstream'
      ? '上游 DeepSeek Key'
      : '未知（非本系统 Key）',
)
const kindTag = computed(() =>
  result.value?.kind === 'unknown' ? 'danger' : 'success',
)
const statusText = computed(() => {
  const s = result.value?.status
  if (s === 'active' || s === 'enabled') return '可用'
  if (s === 'disabled') return '已停用'
  if (s === 'revoked') return '已吊销'
  return s ?? '—'
})

async function verify() {
  if (!plaintext.value.trim()) return
  loading.value = true
  try {
    result.value = await keysApi.verify({ plaintext: plaintext.value.trim() })
  } catch (error) {
    ElMessage.error(error instanceof ApiError ? error.message : '验证失败')
  } finally {
    loading.value = false
  }
}
</script>

<style scoped>
.result {
  margin-top: 12px;
}
</style>
