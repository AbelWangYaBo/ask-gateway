<template>
  <div class="page">
    <div class="page-header">
      <h2>上游 Key 管理</h2>
      <p class="hint">
        DeepSeek 上游 Key 加密入库（AES-GCM）；增删改即时生效，服务与 DSH 池不重启。
        指纹 = SHA-256 前 16 位，可与 DeepSeek 平台侧 Key 比对。
      </p>
      <el-button type="primary" @click="showCreate = true">新增上游 Key</el-button>
    </div>

    <el-table :data="rows" v-loading="loading" border stripe>
      <el-table-column prop="label" label="标签" min-width="120" />
      <el-table-column prop="key_preview" label="Key（前6…后4）" min-width="140">
        <template #default="{ row }">
          <code>{{ row.key_preview }}</code>
        </template>
      </el-table-column>
      <el-table-column prop="key_fingerprint" label="指纹（SHA-256 前 16）" min-width="160">
        <template #default="{ row }">
          <code>{{ row.key_fingerprint }}</code>
        </template>
      </el-table-column>
      <el-table-column label="状态" width="90">
        <template #default="{ row }">
          <el-switch
            :model-value="row.is_active"
            @change="(v: boolean) => toggleActive(row, v)"
          />
        </template>
      </el-table-column>
      <el-table-column prop="notes" label="备注" min-width="140" show-overflow-tooltip />
      <el-table-column prop="created_at" label="创建时间" min-width="170">
        <template #default="{ row }">{{ formatDateTime(row.created_at) }}</template>
      </el-table-column>
      <el-table-column label="操作" width="90" fixed="right">
        <template #default="{ row }">
          <el-popconfirm title="删除后该 Key 的池组立即回收，确认？" @confirm="removeRow(row)">
            <template #reference>
              <el-button type="danger" link>删除</el-button>
            </template>
          </el-popconfirm>
        </template>
      </el-table-column>
    </el-table>

    <el-dialog v-model="showCreate" title="新增上游 Key" width="480">
      <el-form label-width="90">
        <el-form-item label="标签" required>
          <el-input v-model="form.label" placeholder="如 main / backup" maxlength="64" />
        </el-form-item>
        <el-form-item label="Key 明文" required>
          <el-input
            v-model="form.plaintext"
            type="password"
            show-password
            placeholder="sk-..."
          />
        </el-form-item>
        <el-form-item label="备注">
          <el-input v-model="form.notes" placeholder="选填" maxlength="512" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="showCreate = false">取消</el-button>
        <el-button type="primary" :loading="creating" @click="create">创建</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import { upstreamKeysApi } from '@/api'
import type { UpstreamKey } from '@/api/types'
import { formatDateTime } from '@/utils/format'

const rows = ref<UpstreamKey[]>([])
const loading = ref(false)
const showCreate = ref(false)
const creating = ref(false)
const form = ref({ label: '', plaintext: '', notes: '' })

async function load() {
  loading.value = true
  try {
    rows.value = await upstreamKeysApi.list()
  } catch (e) {
    ElMessage.error((e as Error).message)
  } finally {
    loading.value = false
  }
}

async function create() {
  if (!form.value.label.trim() || form.value.plaintext.trim().length < 8) {
    ElMessage.warning('标签必填；Key 明文至少 8 字符')
    return
  }
  creating.value = true
  try {
    await upstreamKeysApi.create({
      label: form.value.label.trim(),
      plaintext: form.value.plaintext.trim(),
      notes: form.value.notes.trim() || null,
    })
    ElMessage.success('已创建并即时生效')
    showCreate.value = false
    form.value = { label: '', plaintext: '', notes: '' }
    await load()
  } catch (e) {
    ElMessage.error((e as Error).message)
  } finally {
    creating.value = false
  }
}

async function toggleActive(row: UpstreamKey, value: boolean) {
  try {
    await upstreamKeysApi.update(row.id, { is_active: value })
    row.is_active = value
    ElMessage.success(value ? '已启用，池组已上线' : '已停用，池组已回收')
  } catch (e) {
    ElMessage.error((e as Error).message)
  }
}

async function removeRow(row: UpstreamKey) {
  try {
    await upstreamKeysApi.remove(row.id)
    ElMessage.success('已删除')
    await load()
  } catch (e) {
    ElMessage.error((e as Error).message)
  }
}

onMounted(load)
</script>

<style scoped>
.page-header {
  display: flex;
  align-items: flex-start;
  gap: 16px;
  margin-bottom: 16px;
}
.page-header h2 {
  margin: 0;
}
.hint {
  flex: 1;
  color: var(--el-text-color-secondary);
  font-size: 12px;
  margin: 4px 0 0;
}
</style>
