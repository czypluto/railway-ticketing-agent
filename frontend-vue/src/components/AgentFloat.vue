<script setup>
/**
 * AgentFloat.vue —— 右下角常驻 AI 助手浮窗（Vue 3 <script setup>，无外部 UI 依赖，自包含）。
 *
 * 能力：
 *  - 悬浮所有页面元素之上，默认贴右下角；拖头部移动，边界不出可视区。
 *  - 可缩放：右下角手柄拖拽调整宽高（有最小/最大与视口约束），移动+尺寸都持久化。
 *  - 头部“收起”缩成机器人小圆球，点小圆球展开恢复。
 *  - 头部“关闭”彻底隐藏，仅本次会话有效，刷新恢复。
 *  - 主体为 Agent 对话（SSE 直连 /py-api/api/chat），正文 14px、高对比、结构化文本可断行。
 */
import { nextTick, onBeforeUnmount, onMounted, reactive, ref } from 'vue'

const props = defineProps({
  endpoint: { type: String, default: '/py-api/api/chat' },
  tokenKey: { type: String, default: 'resv_token' },
  userKey: { type: String, default: 'resv_user' },
  stateKey: { type: String, default: 'agent_float_state' }
})

const MARGIN = 12
const BUB = 48
const MIN_W = 340
const MIN_H = 340

// ---------------- 展示状态 ----------------
const closed = ref(false)
const collapsed = ref(false)
const dragging = ref(false)
const sizing = ref(false)
const pos = reactive({ left: 0, top: 0 })
const size = reactive({ w: 460, h: 660 })
const panelRef = ref(null)
const bubbleRef = ref(null)

function readUser() {
  try {
    const u = JSON.parse(localStorage.getItem(props.userKey) || '{}')
    return { name: u.name || u.username || '', role: u.role || '' }
  } catch {
    return { name: '', role: '' }
  }
}
const user = readUser()

const clamp = (v, lo, hi) => Math.min(Math.max(v, lo), hi)

function saveState() {
  try {
    localStorage.setItem(props.stateKey, JSON.stringify({
      collapsed: collapsed.value,
      left: pos.left, top: pos.top,
      w: size.w, h: size.h
    }))
  } catch { /* 存储不可用时忽略 */ }
}

function loadState() {
  let s = {}
  try { s = JSON.parse(localStorage.getItem(props.stateKey) || '{}') } catch { /* 忽略 */ }
  collapsed.value = !!s.collapsed
  const maxW = Math.max(MIN_W, Math.min(460, window.innerWidth - MARGIN * 2))
  const maxH = Math.max(MIN_H, Math.min(680, window.innerHeight - MARGIN * 2))
  if (typeof s.w === 'number' && typeof s.h === 'number') {
    size.w = clamp(s.w, MIN_W, Math.max(MIN_W, window.innerWidth - MARGIN * 2))
    size.h = clamp(s.h, MIN_H, Math.max(MIN_H, window.innerHeight - MARGIN * 2))
  } else {
    size.w = maxW
    size.h = maxH
  }
  if (typeof s.left === 'number' && typeof s.top === 'number') {
    pos.left = s.left
    pos.top = s.top
  } else {
    pos.left = window.innerWidth - size.w - MARGIN
    pos.top = window.innerHeight - size.h - MARGIN
  }
  clampActive()
}

/** 依当前活动控件实测宽高，把 pos 收进可视区 */
function clampActive() {
  const el = collapsed.value ? bubbleRef.value : panelRef.value
  const w = el ? el.offsetWidth : BUB
  const h = el ? el.offsetHeight : BUB
  pos.left = clamp(pos.left, MARGIN, Math.max(MARGIN, window.innerWidth - w - MARGIN))
  pos.top = clamp(pos.top, MARGIN, Math.max(MARGIN, window.innerHeight - h - MARGIN))
}

function afterToggle() {
  nextTick(() => {
    clampActive()
    saveState()
  })
}
function collapse() { collapsed.value = true; dragging.value = false; afterToggle() }
function expand() { collapsed.value = false; afterToggle() }
function closeAll() { closed.value = true }

// ---------------- 移动（头部 / 圆球） ----------------
let drag = null
function onMove(e) {
  if (!drag) return
  const dx = e.clientX - drag.startX
  const dy = e.clientY - drag.startY
  if (Math.abs(dx) + Math.abs(dy) > 3) drag.moved = true
  if (!drag.moved) return
  const el = drag.el
  const w = el ? el.offsetWidth : BUB
  const h = el ? el.offsetHeight : BUB
  pos.left = clamp(drag.oL + dx, MARGIN, Math.max(MARGIN, window.innerWidth - w - MARGIN))
  pos.top = clamp(drag.oT + dy, MARGIN, Math.max(MARGIN, window.innerHeight - h - MARGIN))
}
function endDrag() {
  const d = drag
  drag = null
  dragging.value = false
  window.removeEventListener('pointermove', onMove)
  window.removeEventListener('pointerup', endDrag)
  saveState()
  if (d && d.isBubble && !d.moved) expand()
}
function beginDrag(e) {
  if (e.button !== undefined && e.button !== 0) return
  const isBubble = e.currentTarget === bubbleRef.value
  // 头部拖拽要避开按钮/输入等控件；圆球本身整体可拖、可点开
  if (!isBubble && e.target.closest('button, textarea, a')) return
  e.preventDefault()
  const el = isBubble ? bubbleRef.value : panelRef.value
  drag = { el, isBubble, startX: e.clientX, startY: e.clientY, oL: pos.left, oT: pos.top, moved: false }
  dragging.value = true
  window.addEventListener('pointermove', onMove)
  window.addEventListener('pointerup', endDrag)
}

// ---------------- 缩放（右下角手柄） ----------------
let resize = null
function onResizeMove(e) {
  if (!resize) return
  const dw = e.clientX - resize.startX
  const dh = e.clientY - resize.startY
  const maxW = Math.max(MIN_W, window.innerWidth - pos.left - MARGIN)
  const maxH = Math.max(MIN_H, window.innerHeight - pos.top - MARGIN)
  size.w = clamp(resize.sw + dw, MIN_W, maxW)
  size.h = clamp(resize.sh + dh, MIN_H, maxH)
}
function endResize() {
  resize = null
  sizing.value = false
  window.removeEventListener('pointermove', onResizeMove)
  window.removeEventListener('pointerup', endResize)
  saveState()
}
function beginResize(e) {
  if (e.button !== undefined && e.button !== 0) return
  e.preventDefault()
  e.stopPropagation()
  resize = { startX: e.clientX, startY: e.clientY, sw: size.w, sh: size.h }
  sizing.value = true
  window.addEventListener('pointermove', onResizeMove)
  window.addEventListener('pointerup', endResize)
}

function onViewportResize() {
  nextTick(() => {
    size.w = clamp(size.w, MIN_W, Math.max(MIN_W, window.innerWidth - MARGIN * 2))
    size.h = clamp(size.h, MIN_H, Math.max(MIN_H, window.innerHeight - MARGIN * 2))
    clampActive()
  })
}

onMounted(() => {
  loadState()
  saveState()
  resumeLast() // 续上最近一次对话
  window.addEventListener('resize', onViewportResize)
})
onBeforeUnmount(() => {
  window.removeEventListener('resize', onViewportResize)
  window.removeEventListener('pointermove', onMove)
  window.removeEventListener('pointerup', endDrag)
  window.removeEventListener('pointermove', onResizeMove)
  window.removeEventListener('pointerup', endResize)
  if (abortFn) abortFn()
})

// ---------------- 对话（自包含 SSE 客户端） ----------------
const messages = reactive([])
const text = ref('')
const sending = ref(false)
const listRef = ref(null)
let abortFn = null
let stopped = false // 用户是否主动点了「停止」；用于区分“主动中断”与“服务无返回”

const QUICK = ['帮我查明天北京南到上海虹桥的二等座', '我有哪些订单', '儿童票有什么规定']

function scrollBottom() {
  nextTick(() => {
    const el = listRef.value
    if (el) el.scrollTop = el.scrollHeight
  })
}

function stream(message, token, onEvent, onDone) {
  const controller = new AbortController()
  ;(async () => {
    try {
      const resp = await fetch(props.endpoint, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` },
        body: JSON.stringify({ message }),
        signal: controller.signal
      })
      if (!resp.ok) {
        onEvent({ kind: 'error', text: `请求失败（${resp.status}），请重新登录或稍后再试`, model: '' })
        return
      }
      const reader = resp.body.getReader()
      const decoder = new TextDecoder()
      let buf = ''
      for (;;) {
        const { done, value } = await reader.read()
        if (done) break
        buf += decoder.decode(value, { stream: true })
        let idx
        while ((idx = buf.indexOf('\n\n')) >= 0) {
          const chunk = buf.slice(0, idx)
          buf = buf.slice(idx + 2)
          for (const line of chunk.split('\n')) {
            if (!line.startsWith('data: ')) continue
            const p = line.slice(6).trim()
            if (p === '[DONE]') continue
            try { onEvent(JSON.parse(p)) } catch { /* 忽略坏分片 */ }
          }
        }
      }
    } catch (err) {
      if (err.name !== 'AbortError') onEvent({ kind: 'error', text: `连接异常：${err.message}`, model: '' })
    } finally {
      onDone()
    }
  })()
  return () => controller.abort()
}

function onKeydown(e) {
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault()
    send()
  }
}

async function send(raw) {
  const content = (raw ?? text.value).trim()
  if (!content || sending.value) return
  text.value = ''
  const token = localStorage.getItem(props.tokenKey) || ''
  if (!token) {
    messages.push({ role: 'assistant', text: '尚未登录。请先登录后再与购票助手对话。', stage: '', tone: 'error', model: '' })
    scrollBottom()
    return
  }
  messages.push({ role: 'user', text: content })
  const cur = reactive({ role: 'assistant', text: '', stage: '正在处理…', tone: 'normal', model: '' })
  messages.push(cur)
  sending.value = true
  scrollBottom()

  // 后端实际推送的事件类型：think / result / answer / confirm / error
  const onEvent = (ev) => {
    if (ev.kind === 'think') {
      cur.stage = '正在为您办理…'
    } else if (ev.kind === 'error') {
      cur.stage = ''
      cur.text = ev.text
      cur.tone = 'error'
    } else if (ev.kind === 'confirm' || ev.kind === 'result' || ev.kind === 'answer') {
      cur.stage = ''
      cur.text = cur.text ? `${cur.text}\n${ev.text}` : ev.text
      cur.tone = ev.kind === 'confirm' ? 'confirm' : ev.kind === 'result' ? 'success' : 'normal'
    }
    if (ev.model) cur.model = ev.model
    scrollBottom()
  }
  const onDone = () => {
    sending.value = false
    if (stopped) {
      // 主动停止不是失败：此前会被判成“服务未返回结果”弹红色报错
      if (!cur.text) cur.text = '已停止。'
      cur.tone = 'normal'
      stopped = false
    } else if (!cur.text) {
      cur.text = '服务未返回结果，请重试。'
      cur.tone = 'error'
    }
    scrollBottom()
  }
  abortFn = stream(content, token, onEvent, onDone)
}

function stopStream() {
  stopped = true
  if (abortFn) abortFn()
  sending.value = false
}

// ---------------- 会话历史（服务端每轮已持久化，这里负责“找回/新建”） ----------------
async function histApi(path, method = 'GET') {
  const token = localStorage.getItem(props.tokenKey) || ''
  const resp = await fetch(`${props.endpoint}/history${path}`, {
    method,
    headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` }
  })
  let j = null
  try { j = await resp.json() } catch { /* 非 JSON */ }
  if (!resp.ok || !j || j.code !== 0) throw new Error((j && j.msg) || '历史请求失败')
  return j.data
}

/** 打开/刷新后自动续接最近一次会话，让对话“记得住”。 */
async function resumeLast() {
  if (sending.value) return
  try {
    const { sessions } = await histApi('')
    const s = (sessions || [])[0]
    if (!s) return
    const { messages: msgs } = await histApi(`/${encodeURIComponent(s.id)}`)
    messages.length = 0
    for (const m of msgs || []) {
      messages.push({
        role: m.role === 'user' ? 'user' : 'assistant',
        text: (m.text || '').trim(),
        stage: '',
        tone: 'normal',
        model: ''
      })
    }
    if (messages.length) scrollBottom()
  } catch { /* 未登录/无历史则保持空态 */ }
}

/** 开始新对话：另起会话（记忆按会话隔离，旧会话切回仍在）。 */
async function resetChat() {
  if (sending.value) return
  try {
    await histApi('/reset', 'POST')
    messages.length = 0
  } catch { /* 忽略 */ }
}

// ---- 历史会话切换器（对标 DeepSeek：点旧会话=激活并继续）----
const histOpen = ref(false)
const histSessions = ref([])

async function toggleHist() {
  histOpen.value = !histOpen.value
  if (histOpen.value) {
    try {
      const { sessions } = await histApi('')
      histSessions.value = sessions || []
    } catch { histSessions.value = [] }
  }
}

async function switchSession(s) {
  if (sending.value) return
  try {
    await histApi(`/activate/${encodeURIComponent(s.id)}`, 'POST')
    const { messages: msgs } = await histApi(`/${encodeURIComponent(s.id)}`)
    messages.length = 0
    for (const m of msgs || []) {
      messages.push({
        role: m.role === 'user' ? 'user' : 'assistant',
        text: (m.text || '').trim(),
        stage: '',
        tone: 'normal',
        model: ''
      })
    }
    histOpen.value = false
    scrollBottom()
  } catch { /* 切换失败则保持现状 */ }
}

async function newChatHere() {
  histOpen.value = false
  await resetChat()
}
</script>

<template>
  <Teleport to="body">
    <!-- 完整浮窗 -->
    <div
      v-if="!closed && !collapsed"
      ref="panelRef"
      class="af-panel"
      :class="{ 'af-dragging': dragging, 'af-sizing': sizing }"
      :style="{ left: pos.left + 'px', top: pos.top + 'px', width: size.w + 'px', height: size.h + 'px' }"
    >
      <header class="af-head" @pointerdown="beginDrag">
        <span class="af-bot" aria-hidden="true">
          <svg viewBox="0 0 24 24" width="16" height="16"><path d="M4 7h4V4h8v3h4v7h-3v4H7v-4H4V7z" fill="currentColor"/><circle cx="9" cy="11" r="1.2" fill="#fff"/><circle cx="15" cy="11" r="1.2" fill="#fff"/></svg>
        </span>
        <span class="af-title">AI 购票助手</span>
        <span v-if="user.name" class="af-who">{{ user.name }}{{ user.role ? ' · ' + user.role : '' }}</span>
        <span class="af-actions">
          <button type="button" class="af-btn" title="历史会话" aria-label="历史会话" @click="toggleHist">
            <svg viewBox="0 0 24 24" width="13" height="13"><path d="M4 4v5h5" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"/><path d="M4 9a8 8 0 1 0 2.4-5.4" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"/></svg>
          </button>
          <button type="button" class="af-btn" title="收起为小圆球" aria-label="收起" @click="collapse">
            <svg viewBox="0 0 24 24" width="14" height="14"><path d="M6 12h12" stroke="currentColor" stroke-width="2.6" stroke-linecap="round"/></svg>
          </button>
          <button type="button" class="af-btn af-btn-close" title="关闭浮窗" aria-label="关闭" @click="closeAll">
            <svg viewBox="0 0 24 24" width="14" height="14"><path d="M6 6l12 12M18 6L6 18" stroke="currentColor" stroke-width="2.4" stroke-linecap="round"/></svg>
          </button>
        </span>
      </header>

      <!-- 历史会话切换器 -->
      <div v-if="histOpen" class="af-hist">
        <div class="af-hist-hd">
          <span>历史会话</span>
          <button type="button" class="af-x" aria-label="关闭历史" @click="histOpen = false">×</button>
        </div>
        <div class="af-hist-list">
          <button
            v-for="s in histSessions"
            :key="s.id"
            type="button"
            class="af-hist-it"
            :disabled="sending"
            @click="switchSession(s)"
          >
            <span class="af-hist-p">{{ s.preview || s.id }}</span>
            <span class="af-hist-m">{{ s.count }} 轮 · {{ new Date(s.startedAt * 1000).toLocaleString() }}</span>
          </button>
          <div v-if="!histSessions.length" class="af-hist-empty">暂无历史会话</div>
        </div>
        <button type="button" class="af-hist-new" :disabled="sending" @click="newChatHere">＋ 开始新对话</button>
      </div>

      <div class="af-body">
        <div ref="listRef" class="af-msgs">
          <div v-if="!messages.length" class="af-empty">
            <div>一句话描述购票需求，例如“帮我查明天北京到上海虹桥的二等座”。</div>
            <div class="af-empty-acts">
              <button type="button" class="af-link" :disabled="sending" @click="resumeLast">载入上次对话</button>
              <span class="af-sep">·</span>
              <button type="button" class="af-link" :disabled="sending" @click="resetChat">开始新对话</button>
            </div>
          </div>
          <div
            v-for="(m, i) in messages"
            :key="i"
            class="af-msg"
            :class="m.role === 'user' ? 'af-user' : 'af-assist af-tone-' + m.tone"
          >
            <div class="af-bubble">
              <span v-if="m.stage" class="af-stage">{{ m.stage }}</span>
              <div v-if="m.text" class="af-text">{{ m.text }}</div>
              <div v-if="m.model" class="af-meta">经 {{ m.model }}</div>
            </div>
          </div>
        </div>

        <div v-if="!sending" class="af-quick">
          <button v-for="q in QUICK" :key="q" type="button" class="af-chip" @click="send(q)">{{ q }}</button>
        </div>

        <div class="af-input">
          <textarea
            v-model="text"
            :disabled="sending"
            rows="2"
            placeholder="输入需求，Enter 发送，Shift+Enter 换行"
            @keydown="onKeydown"
          ></textarea>
          <div class="af-acts">
            <button v-if="sending" type="button" class="af-act" @click="stopStream">停止</button>
            <button type="button" class="af-act af-send" :disabled="sending || !text.trim()" @click="send()">发送</button>
          </div>
        </div>
      </div>

      <!-- 右下角缩放手柄 -->
      <span
        class="af-resize"
        title="拖动调整大小"
        aria-hidden="true"
        @pointerdown="beginResize"
      >
        <svg viewBox="0 0 16 16" width="11" height="11"><path d="M14 10v4h-4M14 5v3l-6 6H5" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/></svg>
      </span>
    </div>

    <!-- 收起的小圆球 -->
    <button
      v-else-if="!closed && collapsed"
      ref="bubbleRef"
      type="button"
      class="af-fab"
      :class="{ 'af-dragging': dragging }"
      :style="{ left: pos.left + 'px', top: pos.top + 'px' }"
      aria-label="展开购票助手"
      @pointerdown="beginDrag"
    >
      <svg viewBox="0 0 24 24" width="22" height="22" aria-hidden="true"><path d="M4 7h4V4h8v3h4v7h-3v4H7v-4H4V7z" fill="currentColor"/><circle cx="9" cy="11" r="1.3" fill="#fff"/><circle cx="15" cy="11" r="1.3" fill="#fff"/></svg>
    </button>
  </Teleport>
</template>

<style scoped>
/* 政务蓝基调，自包含；正文偏大、高对比、结构化文本可断行 */
.af-panel,
.af-fab {
  position: fixed;
  z-index: 99999;
  box-sizing: border-box;
}

.af-panel {
  display: flex;
  flex-direction: column;
  background: #fff;
  border: 1px solid #d5dce5;
  border-radius: 8px;
  box-shadow: 0 10px 34px rgba(23, 59, 107, 0.22);
  overflow: hidden;
  font-family: 'Microsoft YaHei', 'PingFang SC', 'Helvetica Neue', Arial, sans-serif;
  color: #22303c;
  touch-action: none;
}
.af-dragging {
  user-select: none;
  cursor: grabbing;
}
.af-sizing {
  user-select: none;
  cursor: nwse-resize;
}

/* ---------- 头部 ---------- */
.af-head {
  flex: none;
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 0 8px 0 12px;
  height: 44px;
  background: linear-gradient(180deg, #1e4e8c, #173b6b);
  color: #fff;
  cursor: grab;
}
.af-head:active {
  cursor: grabbing;
}
.af-bot {
  display: inline-flex;
  color: #fff;
}
.af-title {
  font-size: 15px;
  font-weight: 600;
  letter-spacing: 0.5px;
  white-space: nowrap;
}
.af-who {
  flex: 1;
  min-width: 0;
  text-align: right;
  font-size: 12px;
  color: rgba(255, 255, 255, 0.85);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.af-actions {
  display: inline-flex;
  gap: 4px;
}
.af-btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 26px;
  height: 26px;
  border: none;
  border-radius: 4px;
  background: rgba(255, 255, 255, 0.14);
  color: #fff;
  cursor: pointer;
  transition: background 0.15s;
}
.af-btn:hover {
  background: rgba(255, 255, 255, 0.32);
}
.af-btn-close:hover {
  background: #b23a3a;
}
.af-btn:focus-visible,
.af-act:focus-visible,
.af-chip:focus-visible {
  outline: 2px solid #fff;
  outline-offset: 1px;
}

/* ---------- 历史会话切换器 ---------- */
.af-hist {
  position: absolute;
  top: 46px;
  right: 8px;
  width: 244px;
  max-width: calc(100% - 16px);
  z-index: 5;
  display: flex;
  flex-direction: column;
  background: #fff;
  border: 1px solid #d5dce5;
  border-radius: 6px;
  box-shadow: 0 8px 22px rgba(23, 59, 107, 0.18);
  overflow: hidden;
}
.af-hist-hd {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 6px 8px 6px 10px;
  font-size: 12px;
  color: #5f7083;
  border-bottom: 1px solid #eef1f5;
  background: #fbfcfe;
}
.af-x {
  border: none;
  background: none;
  color: #8a97a6;
  font-size: 15px;
  line-height: 1;
  cursor: pointer;
  padding: 0 2px;
}
.af-hist-list {
  max-height: 220px;
  overflow-y: auto;
  padding: 4px;
}
.af-hist-it {
  display: block;
  width: 100%;
  text-align: left;
  border: none;
  background: none;
  padding: 6px 7px;
  border-radius: 4px;
  cursor: pointer;
}
.af-hist-it:hover {
  background: #f2f6fc;
}
.af-hist-it:disabled {
  opacity: 0.5;
  cursor: default;
}
.af-hist-p {
  display: block;
  color: #22303c;
  font-size: 12px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.af-hist-m {
  display: block;
  color: #9aa7b5;
  font-size: 11px;
  margin-top: 2px;
}
.af-hist-empty {
  color: #9aa7b5;
  font-size: 12px;
  text-align: center;
  padding: 14px;
}
.af-hist-new {
  display: block;
  width: 100%;
  border: none;
  border-top: 1px solid #eef1f5;
  background: #fbfcfe;
  color: #1e4e8c;
  font-size: 13px;
  padding: 7px;
  cursor: pointer;
}
.af-hist-new:hover {
  background: #f2f6fc;
}
.af-hist-new:disabled {
  opacity: 0.5;
  cursor: default;
}

/* ---------- 对话区 ---------- */
.af-body {
  flex: 1;
  min-height: 0;
  display: flex;
  flex-direction: column;
}
.af-msgs {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  padding: 14px 12px 8px;
  background: #e9eef5; /* 独立聊天区底色，气泡与“浮窗面板”清楚分层 */
}
.af-empty {
  margin-top: 60px;
  text-align: center;
  color: #5f7083;
  font-size: 14px;
  line-height: 1.9;
  padding: 0 16px;
}
.af-empty-acts {
  margin-top: 14px;
}
.af-link {
  border: none;
  background: none;
  padding: 2px 4px;
  color: #1e4e8c;
  font-size: 13px;
  cursor: pointer;
  text-decoration: underline;
  text-underline-offset: 2px;
}
.af-link:hover {
  color: #173b6b;
}
.af-link:disabled {
  opacity: 0.5;
  cursor: default;
}
.af-sep {
  margin: 0 6px;
  color: #9aa7b5;
}
.af-msg {
  display: flex;
  margin-bottom: 12px;
}
.af-user {
  justify-content: flex-end;
}
.af-user .af-bubble {
  background: linear-gradient(180deg, #1e4e8c, #1a4580);
  color: #fff;
  box-shadow: 0 1px 3px rgba(23, 59, 107, 0.2);
}
.af-assist .af-bubble {
  background: #fff;
  border: 1px solid #cdd8e6;
  box-shadow: 0 1px 3px rgba(23, 59, 107, 0.1);
}
.af-tone-error .af-bubble {
  background: #fbeeee;
  border-color: #e8b7b7;
}

.af-tone-confirm .af-bubble {
  background: #eaf2ff;
  border-color: #b9cff2;
}
.af-tone-success .af-bubble {
  background: #edf7ed;
  border-color: #b8dbb8;
}
.af-bubble {
  max-width: 100%;
  padding: 9px 13px;
  border-radius: 8px;
  font-size: 14px;
  line-height: 1.75;
  color: #22303c;
}
.af-user .af-bubble {
  max-width: 88%; /* 用户话为右侧胶囊，不铺满 */
}
.af-user .af-bubble {
  color: #fff;
}
.af-stage {
  display: block;
  color: #6b7a8c;
  font-size: 13px;
}
.af-user .af-stage {
  color: rgba(255, 255, 255, 0.85);
}
.af-text {
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  word-break: break-word;
}
.af-meta {
  margin-top: 6px;
  padding-top: 5px;
  border-top: 1px dashed rgba(120, 138, 158, 0.35);
  font-size: 11px;
  color: #8a97a6;
}
.af-user .af-meta {
  border-top-color: rgba(255, 255, 255, 0.3);
  color: rgba(255, 255, 255, 0.75);
}

/* ---------- 快捷 & 输入 ---------- */
.af-quick {
  flex: none;
  padding: 6px 10px 8px;
  border-top: 1px dashed #e2e7ee;
  background: #fbfcfe;
}
.af-chip {
  margin: 2px 6px 2px 0;
  padding: 4px 10px;
  border: 1px solid #d5dce5;
  border-radius: 14px;
  background: #fff;
  color: #1e4e8c;
  font-size: 13px;
  cursor: pointer;
}
.af-chip:hover {
  border-color: #1e4e8c;
  background: #f4f8ff;
}
.af-input {
  flex: none;
  padding: 10px 34px 12px 10px;
  border-top: 1px solid #d5dce5;
  background: #fff;
}
.af-input textarea {
  display: block;
  width: 100%;
  box-sizing: border-box;
  padding: 8px 10px;
  border: 1px solid #ccd5e0;
  border-radius: 5px;
  resize: none;
  font: inherit;
  font-size: 14px;
  line-height: 1.6;
  color: #22303c;
  background: #fff;
}
.af-input textarea:focus {
  outline: none;
  border-color: #1e4e8c;
  box-shadow: 0 0 0 2px rgba(30, 78, 140, 0.12);
}
.af-input textarea:disabled {
  background: #f2f4f7;
  color: #9aa7b5;
}
.af-acts {
  margin-top: 8px;
  text-align: right;
}
.af-act {
  margin-left: 8px;
  padding: 5px 16px;
  border: 1px solid #ccd5e0;
  border-radius: 4px;
  background: #fff;
  color: #173b6b;
  font-size: 14px;
  cursor: pointer;
}
.af-act:hover {
  border-color: #1e4e8c;
}
.af-send {
  background: #1e4e8c;
  border-color: #1e4e8c;
  color: #fff;
}
.af-send:hover {
  background: #173b6b;
}
.af-act:disabled {
  opacity: 0.55;
  cursor: not-allowed;
}

/* ---------- 缩放手柄 ---------- */
.af-resize {
  position: absolute;
  right: 2px;
  bottom: 2px;
  width: 16px;
  height: 16px;
  display: inline-flex;
  align-items: flex-end;
  justify-content: flex-end;
  color: #9aa7b5;
  cursor: nwse-resize;
  z-index: 2;
}
.af-resize:hover {
  color: #1e4e8c;
}

/* ---------- 收起小圆球（FAB，勿与消息 .af-bubble 同名冲突） ---------- */
.af-fab {
  width: 48px;
  height: 48px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  border: none;
  border-radius: 50%;
  background: linear-gradient(180deg, #1e4e8c, #173b6b);
  color: #fff;
  box-shadow: 0 4px 16px rgba(23, 59, 107, 0.35);
  cursor: grab;
  touch-action: none;
  padding: 0;
}
.af-fab:active,
.af-fab.af-dragging {
  cursor: grabbing;
}
.af-fab:hover {
  box-shadow: 0 6px 20px rgba(23, 59, 107, 0.42);
}
</style>
