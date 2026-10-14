// 与两个后端的通信。Java 登录/购票，Python 对话（SSE）。
const TOKEN_KEY = 'resv_token'

async function jfetch(path, options = {}) {
  const resp = await fetch(path, {
    ...options,
    headers: {
      'Content-Type': 'application/json',
      Authorization: `Bearer ${getToken()}`,
      ...(options.headers || {})
    }
  })
  const body = await resp.json()
  if (body.code !== 0) throw new Error(body.msg || '请求失败')
  return body.data
}

export function saveToken(token) {
  localStorage.setItem(TOKEN_KEY, token)
}

export function clearToken() {
  localStorage.removeItem(TOKEN_KEY)
}

export function getToken() {
  return localStorage.getItem(TOKEN_KEY) || ''
}

/** Java 登录，返回 data: { token, name, department, position, ... } */
export async function login(username, password) {
  const resp = await fetch('/java-api/api/auth/login', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ username, password })
  })
  const body = await resp.json()
  if (body.code !== 0) {
    throw new Error(body.msg || '登录失败')
  }
  return body.data
}

/**
 * Python 对话（SSE）。逐条回调 onEvent({kind,text,model})；结束回调 onDone()。
 * 返回 abort 函数。
 */
export function chatStream(message, token, onEvent, onDone) {
  const controller = new AbortController()

  fetch('/py-api/api/chat', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      Authorization: `Bearer ${token}`
    },
    body: JSON.stringify({ message }),
    signal: controller.signal
  })
    .then(async (resp) => {
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
        // SSE 行
        let idx
        while ((idx = buf.indexOf('\n\n')) >= 0) {
          const chunk = buf.slice(0, idx)
          buf = buf.slice(idx + 2)
          for (const line of chunk.split('\n')) {
            if (!line.startsWith('data: ')) continue
            const payload = line.slice(6).trim()
            if (payload === '[DONE]') continue
            try {
              onEvent(JSON.parse(payload))
            } catch (_) {
              // 忽略解析失败的分片
            }
          }
        }
      }
    })
    .catch((err) => {
      if (err.name !== 'AbortError') {
        onEvent({ kind: 'error', text: `连接异常：${err.message}`, model: '' })
      }
    })
    .finally(onDone)

  return () => controller.abort()
}

// ---------- 铁路购票（Java） ----------

export async function listStations(kw = '') {
  return jfetch(`/java-api/api/rail/stations${kw ? `?kw=${encodeURIComponent(kw)}` : ''}`)
}

export async function queryTickets(from, to, date, seatClass = '') {
  const p = new URLSearchParams({ from, to, date })
  if (seatClass) p.set('seatClass', seatClass)
  return jfetch(`/java-api/api/ticket/query?${p.toString()}`)
}

export async function buyTicket({ tripId, seatClass, fromStationId, toStationId, contactId = null }) {
  return jfetch('/java-api/api/ticket/buy', {
    method: 'POST',
    body: JSON.stringify({ tripId, seatClass, fromStationId, toStationId, contactId })
  })
}

export async function myOrders() {
  return jfetch('/java-api/api/ticket/orders/my')
}

export async function payOrder(requestId) {
  return jfetch(`/java-api/api/ticket/orders/${requestId}/pay`, { method: 'POST' })
}

export async function cancelOrder(requestId) {
  return jfetch(`/java-api/api/ticket/orders/${requestId}/cancel`, { method: 'POST' })
}

export async function listContacts() {
  return jfetch('/java-api/api/user/contacts')
}

// ---------- 对话档案（Python） ----------

export async function chatHistoryList() {
  return jfetch('/py-api/api/chat/history')
}

export async function chatHistoryDetail(session) {
  return jfetch(`/py-api/api/chat/history/${encodeURIComponent(session)}`)
}

export async function chatHistoryReset() {
  return jfetch('/py-api/api/chat/history/reset', { method: 'POST' })
}

/** 切换到指定历史会话：之后的续聊（档案+记忆）都落在该会话 */
export async function chatHistoryActivate(session) {
  return jfetch(`/py-api/api/chat/history/activate/${encodeURIComponent(session)}`, { method: 'POST' })
}

export async function chatHistoryDelete(session) {
  return jfetch(`/py-api/api/chat/history/${encodeURIComponent(session)}`, { method: 'DELETE' })
}

// ---------- 知识库管理（仅 ADMIN） ----------

export async function kbList() {
  return jfetch('/py-api/api/admin/kb')
}

/** 上传知识库文件：name=文件名，contentB64=文件内容 base64（md 顶部可用 dept_tags 声明可见性） */
export async function kbUpload(name, contentB64) {
  return jfetch('/py-api/api/admin/kb/upload', { method: 'POST', body: JSON.stringify({ name, content: contentB64 }) })
}

export async function kbDelete(file) {
  return jfetch(`/py-api/api/admin/kb?file=${encodeURIComponent(file)}`, { method: 'DELETE' })
}

export async function kbReingest() {
  return jfetch('/py-api/api/admin/kb/reingest', { method: 'POST' })
}
