<script setup>
import { reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { login, saveToken } from '../api'

const emit = defineEmits(['login'])
const form = reactive({ username: '', password: '' })
const loading = ref(false)

async function submit() {
  if (!form.username || !form.password) {
    ElMessage.warning('请输入账号和密码')
    return
  }
  loading.value = true
  try {
    const data = await login(form.username, form.password)
    saveToken(data.token)
    const user = {
      userId: data.userId,
      name: data.name,
      department: data.department,
      position: data.position,
      role: data.role
    }
    localStorage.setItem('resv_user', JSON.stringify(user))
    emit('login', user)
  } catch (e) {
    ElMessage.error(e.message)
  } finally {
    loading.value = false
  }
}
</script>

<template>
  <div class="login-wrap">
    <div class="login-card">
      <div class="login-title">铁路购票系统</div>
      <el-form label-position="top" @submit.prevent>
        <el-form-item label="账号">
          <el-input v-model="form.username" placeholder="请输入账号" autocomplete="username" />
        </el-form-item>
        <el-form-item label="密码">
          <el-input
            v-model="form.password"
            type="password"
            placeholder="请输入密码"
            show-password
            autocomplete="current-password"
            @keyup.enter="submit"
          />
        </el-form-item>
        <el-button
          class="login-btn"
          type="primary"
          :loading="loading"
          native-type="button"
          @click="submit"
        >
          登 录
        </el-button>
      </el-form>
      <div class="login-tip">
        演示账号：zhanggong / 123456（技术部 · 购票业务）；wangzong / 123456（管理员 · 另含知识库与铁路管理）
      </div>
    </div>
  </div>
</template>

<style scoped>
.login-wrap {
  height: 100%;
  display: flex;
  align-items: center;
  justify-content: center;
  background: linear-gradient(160deg, #173b6b 0%, #1e4e8c 60%, #2a5f9e 100%);
}
.login-card {
  width: 380px;
  padding: 36px 32px 24px;
  background: #fff;
  border-radius: 4px;
}
.login-title {
  font-size: 18px;
  text-align: center;
  margin-bottom: 24px;
  color: var(--resv-blue-dark);
}
.login-btn {
  width: 100%;
  margin-top: 4px;
}
.login-tip {
  margin-top: 16px;
  font-size: 12px;
  color: #8592a5;
  line-height: 1.6;
}
</style>
