<script setup>
import { nextTick, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { chatHistoryActivate, chatHistoryDelete, chatHistoryDetail, chatHistoryList, chatHistoryReset, chatStream, clearToken } from '../api'

const props = defineProps({
  user: { type: Object, required: true },
  embedded: { type: Boolean, default: false }
})
const emit = defineEmits(['logout'])

const messages = reactive([]) // { role, text, stage, tone, model }
const input = ref('')
const sending = ref(false)
const listRef = ref(null)
let abort = null
let stopped = false // 用户是否主动点了「停止」；用于区分“主动中断”与“服务无返回”

const QUICK = [
  '帮我查明天北京南到上海虹桥的二等座',
  '儿童票有什么规定',
  '我有哪些订单'
]

function scrollBottom() {
  nextTick(() => {
    const el = listRef.value
    if (el) el.scrollTop = el.scrollHeight
  })
}

// 事件与收尾处理抽出来，供「发消息」和「确认/取消按钮」两条路径共用
function makeHandlers(current) {
  // 后端实际推送的事件类型：think / result / answer / confirm / error
  // （见 backend-python/app/agent/orchestrator.py 中的 _event 调用）
  const onEvent = (ev) => {
    if (ev.kind === 'think') {
      current.stage = '正在为您办理…'
    } else if (ev.kind === 'error') {
      current.stage = ''
      current.text = ev.text
      current.tone = 'error'
    } else if (ev.kind === 'confirm') {
      current.stage = ''
      current.text = current.text ? `${current.text}\n${ev.text}` : ev.text
      current.tone = 'normal'
      // 结构化确认：存下 intent_id，由「确认下单 / 取消」按钮原样回传
      if (ev.intent_id) current.intentId = ev.intent_id
    } else if (ev.kind === 'result' || ev.kind === 'answer') {
      current.stage = ''
      // 追加而非覆盖，避免后续事件把查询结果冲掉
      current.text = current.text ? `${current.text}\n${ev.text}` : ev.text
      current.tone = ev.kind === 'result' ? 'success' : 'normal'
    }
    // 下单成功后后端**在 result 事件上**带回 requestId，据此渲染「支付」按钮（不必让用户打字）。
    // 这一行原先写在 confirm 分支里，于是整页助手的支付按钮永远不出现 ——
    // 文案说着"请在下方完成支付"，按钮却没有；浮窗（AgentFloat）写在分支外所以正常。
    if (ev.pay_request_id) current.payRequestId = ev.pay_request_id
    current.model = ev.model || current.model
    scrollBottom()
  }
  const onDone = () => {
    sending.value = false
    if (stopped) {
      // 主动停止不是失败：此前会被判成“服务未返回结果”弹红色报错
      current.stage = ''
      if (!current.text) current.text = '已停止。'
      current.tone = 'normal'
      stopped = false
    } else if (!current.text) {
      current.stage = ''
      current.text = '服务未返回结果，请重试。'
      current.tone = 'error'
    }
    scrollBottom()
  }
  return { onEvent, onDone }
}

function pushAssistant() {
  // intentId / payRequestId 一开始就声明，保证后面赋值时是已追踪的属性
  const current = { role: 'assistant', text: '', stage: '正在处理…', tone: 'normal', model: '', intentId: '', payRequestId: '' }
  messages.push(current)
  return current
}

/** 点「支付」：走显式的 action=pay 载荷，同样不经过自然语言。 */
async function payOrder(msg) {
  if (sending.value) return
  const rid = msg.payRequestId
  if (!rid) return
  msg.payRequestId = ''                      // 用掉即清，防止重复支付
  const token = localStorage.getItem('resv_token')
  if (!token) return

  sending.value = true
  messages.push({ role: 'user', text: '［支付］' })
  const current = pushAssistant()
  scrollBottom()
  const { onEvent, onDone } = makeHandlers(current)
  abort = chatStream('', token, onEvent, onDone, { action: 'pay', confirm: true })
}

/**
 * 点「确认下单 / 取消」：带上 intent_id 走结构化确认路径，不经过自然语言。
 * 这也意味着「好的，但我想改时间」这类句子不会再触发出票。
 */
async function decideBuy(msg, confirm) {
  if (sending.value) return
  const intentId = msg.intentId
  if (!intentId) return
  msg.intentId = ''                          // 用掉即清，防止重复点击重复提交
  const token = localStorage.getItem('resv_token')
  if (!token) return

  sending.value = true
  messages.push({ role: 'user', text: confirm ? '［确认下单］' : '［取消下单］' })
  const current = pushAssistant()
  scrollBottom()
  const { onEvent, onDone } = makeHandlers(current)
  abort = chatStream('', token, onEvent, onDone, { confirm, intent_id: intentId })
}

async function send(text) {
  const content = (text ?? input.value).trim()
  if (!content || sending.value) return
  input.value = ''
  sending.value = true

  messages.push({ role: 'user', text: content })
  const current = pushAssistant()
  scrollBottom()
  const { onEvent, onDone } = makeHandlers(current)
  abort = chatStream(content, localStorage.getItem('resv_token'), onEvent, onDone)
}

function onKeydown(e) {
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault()
    send()
  }
}

function stopStream() {
  stopped = true
  if (abort) abort()
  sending.value = false
}

function logout() {
  clearToken()
  localStorage.removeItem('resv_user')
  emit('logout')
}

// ---- 对话档案：历史回放 / 新对话 ----
const historyVisible = ref(false)
const sessions = ref([])
const replay = ref([])
const replayTitle = ref('')

async function openHistory() {
  try {
    const d = await chatHistoryList()
    sessions.value = d.sessions || []
    historyVisible.value = true
  } catch (e) {
    ElMessage.error(e.message)
  }
}

async function loadSession(s) {
  try {
    const d = await chatHistoryDetail(s.id)
    replay.value = d.messages || []
    replayTitle.value = s.preview || s.id
  } catch (e) {
    ElMessage.error(e.message)
  }
}

/** “继续此会话”：激活该会话并把其历史载入主对话区，后续发送都写入该会话 */
async function continueSession(s) {
  try {
    await chatHistoryActivate(s.id)
    const d = await chatHistoryDetail(s.id)
    messages.length = 0
    for (const m of d.messages || []) {
      messages.push({
        role: m.role === 'user' ? 'user' : 'assistant',
        text: m.text || '',
        stage: '',
        tone: 'normal',
        model: ''
      })
    }
    historyVisible.value = false
    scrollBottom()
    ElMessage.success(`已切换至该会话，续聊会写入「${s.preview || s.id}」`)
  } catch (e) {
    ElMessage.error(e.message)
  }
}

async function removeSession(s) {
  try {
    await chatHistoryDelete(s.id)
    sessions.value = sessions.value.filter((x) => x.id !== s.id)
    if (replayTitle.value === s.preview) {
      replay.value = []
      replayTitle.value = ''
    }
  } catch (e) {
    ElMessage.error(e.message)
  }
}

async function newConversation() {
  try {
    await chatHistoryReset()
    messages.length = 0
    ElMessage.success('已开始新对话')
  } catch (e) {
    ElMessage.error(e.message)
  }
}
</script>

<template>
  <div class="page">
    <header v-if="!embedded" class="topbar">
      <div class="brand">铁路购票系统</div>
      <div class="user">
        <span>{{ user.name }}（{{ user.role }}）</span>
        <el-button link type="primary" @click="logout">退出</el-button>
      </div>
    </header>

    <main class="main">
      <section class="chat">
        <div class="tools">
          <el-button size="small" @click="openHistory">历史回放</el-button>
          <el-button size="small" :disabled="sending" @click="newConversation">新对话</el-button>
        </div>
        <div ref="listRef" class="msg-list">
          <div
            v-for="(m, i) in messages"
            :key="i"
            class="msg"
            :class="m.role === 'user' ? 'msg-user' : `msg-assist msg-${m.tone}`"
          >
            <div class="bubble">
              <span v-if="m.stage" class="stage">{{ m.stage }}</span>
              <div v-if="m.text" class="text">{{ m.text }}</div>
              <!-- 结构化确认：按钮直接把 intent_id 带回后端，用户不必打字「确认」 -->
              <div v-if="m.intentId && !sending" class="confirm-acts">
                <el-button type="primary" size="small" @click="decideBuy(m, true)">确认下单</el-button>
                <el-button size="small" @click="decideBuy(m, false)">取消</el-button>
              </div>
              <!-- 支付按钮：同样脱离关键词路径 -->
              <div v-if="m.payRequestId && !sending" class="confirm-acts">
                <el-button type="primary" size="small" @click="payOrder(m)">支付</el-button>
              </div>
              <div v-if="m.model" class="meta">经 {{ m.model }}</div>
            </div>
          </div>
          <div v-if="messages.length === 0" class="empty">
            用一句话描述购票需求，例如“帮我查明天北京到上海的二等座”。
          </div>
        </div>

        <div class="quick" v-if="!sending">
          <button v-for="q in QUICK" :key="q" class="chip" @click="send(q)">{{ q }}</button>
        </div>

        <div class="input-row">
          <el-input
            v-model="input"
            type="textarea"
            :rows="2"
            :disabled="sending"
            placeholder="输入查票/购票需求，Enter 发送，Shift+Enter 换行"
            resize="none"
            @keydown="onKeydown"
          />
          <div class="actions">
            <el-button v-if="sending" @click="stopStream">停止</el-button>
            <el-button type="primary" :disabled="sending || !input.trim()" @click="send()">
              发送
            </el-button>
          </div>
        </div>
      </section>

      <el-dialog v-model="historyVisible" title="历史回放" width="860px" top="6vh">
        <div class="hist">
          <div class="hist-list">
            <div v-for="s in sessions" :key="s.id" class="hist-item">
              <button class="hist-load" @click="loadSession(s)">
                <div class="hist-t">{{ s.preview || s.id }}</div>
                <div class="hist-m">{{ s.count }} 轮 · {{ new Date(s.startedAt * 1000).toLocaleString() }}</div>
              </button>
              <div class="hist-ops">
                <el-button class="hist-go" size="small" type="primary" text @click.stop="continueSession(s)">继续</el-button>
                <el-button class="hist-del" size="small" type="danger" text @click.stop="removeSession(s)">删</el-button>
              </div>
            </div>
            <div v-if="!sessions.length" class="hist-empty">暂无历史对话</div>
          </div>
          <div class="hist-body">
            <div class="hist-title">{{ replayTitle || '点击左侧会话查看' }}</div>
            <div class="hist-msgs">
              <div v-for="(m, i) in replay" :key="i" class="hist-msg">
                <span class="who">{{ m.role === 'user' ? '我' : '助手' }}</span>
                <pre class="txt">{{ m.text }}</pre>
              </div>
              <div v-if="!replay.length" class="hist-empty">选择左侧会话后在此展示</div>
            </div>
          </div>
        </div>
      </el-dialog>
    </main>
  </div>
</template>

<style scoped>
.page {
  height: 100%;
  display: flex;
  flex-direction: column;
}
.topbar {
  height: 52px;
  flex: none;
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0 20px;
  background: #fff;
  border-bottom: 1px solid var(--resv-line);
}
.brand {
  color: var(--resv-blue-dark);
  font-size: 16px;
}
.user {
  font-size: 13px;
  color: #5b6b7d;
}
.main {
  flex: 1;
  min-height: 0;
  padding: 16px 24px 24px;
}
.chat {
  height: 100%;
  max-width: 860px;
  margin: 0 auto;
  display: flex;
  flex-direction: column;
  background: #fff;
  border: 1px solid var(--resv-line);
}
.msg-list {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  padding: 16px;
}
.empty {
  margin-top: 60px;
  text-align: center;
  color: #8592a5;
  font-size: 14px;
}
.msg {
  display: flex;
  margin-bottom: 14px;
}
.msg-user {
  justify-content: flex-end;
}
.msg-user .bubble {
  background: var(--resv-blue);
  color: #fff;
}
.msg-assist .bubble {
  background: #f4f6f9;
  border: 1px solid #e6ebf1;
}

.msg-error .bubble {
  background: #fef0f0;
  border-color: #f3c2c2;
}
.bubble {
  max-width: 70%;
  padding: 8px 12px;
  border-radius: 4px;
  white-space: pre-wrap;
  word-break: break-word;
  line-height: 1.7;
}
.stage {
  color: #7c8a9b;
  font-size: 13px;
}
.meta {
  margin-top: 6px;
  font-size: 11px;
  color: #9aa7b5;
}
/* 结构化确认按钮：直接带回 intent_id，不必让用户打字「确认」 */
.confirm-acts {
  margin-top: 10px;
  display: flex;
  gap: 8px;
  white-space: normal; /* 抵消 .bubble 的 pre-wrap，避免按钮与文字挤在一行 */
}
.quick {
  padding: 4px 12px 8px;
  border-top: 1px dashed #e6ebf1;
}
.chip {
  margin: 0 8px 4px 0;
  padding: 4px 10px;
  border: 1px solid var(--resv-line);
  border-radius: 12px;
  background: #fff;
  color: var(--resv-blue);
  font-size: 12px;
  cursor: pointer;
}
.chip:hover {
  border-color: var(--resv-blue);
}
.input-row {
  flex: none;
  padding: 12px;
  border-top: 1px solid var(--resv-line);
}
.actions {
  margin-top: 8px;
  text-align: right;
}
.tools {
  padding: 6px 12px 0;
  text-align: right;
  border-bottom: 1px solid #f0f3f7;
}
.hist {
  display: flex;
  height: 480px;
  gap: 12px;
}
.hist-list {
  width: 240px;
  overflow-y: auto;
  border-right: 1px solid var(--resv-line);
  padding-right: 8px;
}
.hist-item {
  display: flex;
  align-items: center;
  margin-bottom: 6px;
  border: 1px solid var(--resv-line);
  border-radius: 4px;
  padding: 4px 6px;
}
.hist-load {
  flex: 1;
  text-align: left;
  border: none;
  background: none;
  cursor: pointer;
  padding: 4px;
}
.hist-ops {
  flex: none;
  display: inline-flex;
  flex-direction: column;
  align-items: flex-end;
  gap: 0;
}
.hist-del {
  flex: none;
}
.hist-go {
  flex: none;
}
.hist-t {
  color: #2c3a4a;
  font-size: 13px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.hist-m {
  color: #9aa7b5;
  font-size: 11px;
  margin-top: 2px;
}
.hist-body {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
}
.hist-title {
  font-weight: 600;
  color: var(--resv-blue-dark);
  padding-bottom: 6px;
  border-bottom: 1px solid #eef1f5;
}
.hist-msgs {
  flex: 1;
  overflow-y: auto;
  margin-top: 8px;
}
.hist-msg {
  margin-bottom: 10px;
}
.who {
  font-size: 12px;
  color: var(--resv-blue);
  margin-right: 6px;
}
.hist-msg pre.txt {
  margin: 2px 0 0;
  white-space: pre-wrap;
  word-break: break-word;
  font-family: inherit;
  color: #333;
  background: #f7f9fc;
  padding: 6px 8px;
  border-radius: 4px;
}
.hist-empty {
  color: #9aa7b5;
  text-align: center;
  margin-top: 20px;
}
</style>
