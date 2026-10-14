<script setup>
// 知识库管理（仅 ADMIN）：上传/替换、列表、删除、全部重建。
// 上传同名文件即“替换”（后端先删同名旧向量再入库，不会重复堆积）。
import { onMounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { kbDelete, kbList, kbReingest, kbUpload } from '../api'

const files = ref([])
const loading = ref(false)
const uploading = ref(false)
const reingesting = ref(false)
const fileInput = ref(null)
const deleting = ref('')

const KB_HINT =
  '支持 .md / .docx / .pdf（单文件 ≤ 20MB）。上传同名文件即为替换。' +
  'Markdown 可在正文最前用 "dept_tags: 技术部"（或顶部 ---front matter--- 块）声明部门可见性，未声明默认全员可见。'

function fmtSize(b) {
  if (!b && b !== 0) return '-'
  if (b < 1024) return b + ' B'
  if (b < 1024 * 1024) return (b / 1024).toFixed(1) + ' KB'
  return (b / 1024 / 1024).toFixed(2) + ' MB'
}

async function load() {
  loading.value = true
  try {
    files.value = await kbList()
  } catch (e) {
    ElMessage.error(e.message)
  } finally {
    loading.value = false
  }
}

function pick() {
  fileInput.value && fileInput.value.click()
}

function onPick(e) {
  const f = e.target.files && e.target.files[0]
  e.target.value = ''
  if (!f) return
  if (!/\.(md|docx|doc|pdf)$/i.test(f.name)) {
    ElMessage.error('仅支持 md / docx / doc / pdf')
    return
  }
  if (f.size > 20 * 1024 * 1024) {
    ElMessage.error('文件超过 20MB 上限')
    return
  }
  uploading.value = true
  const rd = new FileReader()
  rd.onload = async () => {
    const b64 = String(rd.result || '').split(',')[1] || ''
    try {
      const d = await kbUpload(f.name, b64)
      ElMessage.success(`已入库「${d.name}」，新增 ${d.added} 段（同名文件已替换）`)
      await load()
    } catch (err) {
      ElMessage.error(err.message)
    } finally {
      uploading.value = false
    }
  }
  rd.onerror = () => {
    uploading.value = false
    ElMessage.error('读取文件失败')
  }
  rd.readAsDataURL(f)
}

async function removeFile(row) {
  try {
    await ElMessageBox.confirm(`确定删除「${row.name}」？将同步清除其在向量库中的段落。`, '删除确认', {
      type: 'warning',
      confirmButtonText: '删除',
      cancelButtonText: '取消'
    })
  } catch {
    return
  }
  deleting.value = row.name
  try {
    await kbDelete(row.name)
    ElMessage.success('已删除')
    await load()
  } catch (e) {
    ElMessage.error(e.message)
  } finally {
    deleting.value = ''
  }
}

async function reingestAll() {
  reingesting.value = true
  try {
    const d = await kbReingest()
    ElMessage.success(`重建完成，共入库 ${d.added} 段`)
    await load()
  } catch (e) {
    ElMessage.error(e.message)
  } finally {
    reingesting.value = false
  }
}

onMounted(load)
</script>

<template>
  <div class="kb">
    <div class="kb-bar">
      <span class="kb-title">知识库管理</span>
      <div class="kb-actions">
        <el-button type="primary" :loading="uploading" @click="pick">上传文件</el-button>
        <el-button :loading="reingesting" :disabled="loading" @click="reingestAll">全部重建</el-button>
        <input ref="fileInput" type="file" accept=".md,.docx,.doc,.pdf" hidden @change="onPick" />
      </div>
    </div>

    <div class="kb-hint">{{ KB_HINT }}</div>

    <el-table :data="files" v-loading="loading" size="small" border class="kb-table">
      <el-table-column prop="name" label="文件名" min-width="260" show-overflow-tooltip />
      <el-table-column label="大小" width="100">
        <template #default="{ row }">{{ fmtSize(row.sizeBytes) }}</template>
      </el-table-column>
      <el-table-column prop="paragraphs" label="向量段落" width="100" align="center" />
      <el-table-column label="更新时间" width="180">
        <template #default="{ row }">
          {{ row.updatedAt ? new Date(row.updatedAt * 1000).toLocaleString() : '-' }}
        </template>
      </el-table-column>
      <el-table-column label="操作" width="100" align="center">
        <template #default="{ row }">
          <el-button size="small" type="danger" text :loading="deleting === row.name" @click="removeFile(row)">
            删除
          </el-button>
        </template>
      </el-table-column>
      <template #empty>
        <div class="kb-empty">data/docs 下暂无文档，点“上传文件”添加第一条知识。</div>
      </template>
    </el-table>
  </div>
</template>

<style scoped>
.kb {
  max-width: 980px;
  margin: 0 auto;
}
.kb-bar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 10px;
}
.kb-title {
  color: var(--resv-blue-dark, #173b6b);
  font-size: 16px;
  font-weight: 600;
}
.kb-actions {
  display: flex;
  gap: 8px;
}
.kb-hint {
  margin-bottom: 12px;
  padding: 8px 12px;
  background: #f4f7fb;
  border: 1px solid #e2e8f0;
  border-radius: 4px;
  color: #5f7083;
  font-size: 12px;
  line-height: 1.8;
}
.kb-empty {
  color: #9aa7b5;
  padding: 20px 0;
}
</style>
