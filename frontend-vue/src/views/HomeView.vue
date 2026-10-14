<script setup>
import { ref } from 'vue'
import { clearToken } from '../api'
import TicketView from './TicketView.vue'
import OrdersView from './OrdersView.vue'
import ChatView from './ChatView.vue'
import KnowledgeView from './KnowledgeView.vue'
import RailwayAdminView from './RailwayAdminView.vue'

const props = defineProps({ user: { type: Object, required: true } })
const emit = defineEmits(['logout'])
const active = ref('buy')
const isAdmin = (props.user.role || '').toUpperCase() === 'ADMIN'

function logout() {
  clearToken()
  localStorage.removeItem('resv_user')
  emit('logout')
}
</script>

<template>
  <div class="page">
    <header class="topbar">
      <div class="brand">铁路购票系统</div>
      <el-menu mode="horizontal" :default-active="active" class="nav" @select="(k) => (active = k)" :router="false">
        <el-menu-item index="buy">在线购票</el-menu-item>
        <el-menu-item index="orders">我的车票</el-menu-item>
        <el-menu-item v-if="isAdmin" index="kb">知识库管理</el-menu-item>
        <el-menu-item v-if="isAdmin" index="rail">铁路管理</el-menu-item>
        <el-menu-item index="assist">智能助手</el-menu-item>
      </el-menu>
      <div class="user">
        <span>{{ user.name }}（{{ user.role }}）</span>
        <el-button link type="primary" @click="logout">退出</el-button>
      </div>
    </header>

    <main class="main">
      <TicketView v-if="active === 'buy'" :user="user" />
      <OrdersView v-else-if="active === 'orders'" />
      <KnowledgeView v-else-if="active === 'kb' && isAdmin" />
      <RailwayAdminView v-else-if="active === 'rail' && isAdmin" />
      <div v-else class="assist-wrap">
        <ChatView :user="user" embedded @logout="logout" />
      </div>
    </main>
  </div>
</template>

<style scoped>
.page { height: 100%; display: flex; flex-direction: column; }
.topbar {
  height: 56px; flex: none; display: flex; align-items: center;
  padding: 0 20px; background: #fff; border-bottom: 1px solid var(--resv-line);
}
.brand { color: var(--resv-blue-dark); font-size: 16px; margin-right: 28px; }
.nav { border-bottom: none; }
.user { margin-left: auto; font-size: 13px; color: #5b6b7d; }
.main { flex: 1; min-height: 0; padding: 16px 24px 24px; }
.assist-wrap { height: 100%; }
</style>
