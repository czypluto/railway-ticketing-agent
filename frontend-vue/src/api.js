// 与两个后端的通信。Java 登录/购票，Python 对话（SSE）。
const TOKEN_KEY = 'resv_token'

/**
 * 本地时区的 YYYY-MM-DD。offsetDays=0 为今天，1 为明天。
 *
 * 不能用 `new Date(Date.now() + d).toISOString().slice(0,10)`：toISOString 走 UTC，
 * 东八区在本地 08:00 之前拿到的还是前一天，"明天"会被预填成今天。
 */
export function localDate(offsetDays = 0) {
  const d = new Date()
  d.setDate(d.getDate() + offsetDays)
  const p = (n) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`
}

const USER_KEY = 'resv_user'

async function jfetch(path, options = {}) {
  const resp = await fetch(path, {
    ...options,
    headers: {
      'Content-Type': 'application/json',
      Authorization: `Bearer ${getToken()}`,
      ...(options.headers || {})
    }
  })
  // 后端异常时可能是空 body 或非 JSON，直接 resp.json() 会抛原生 SyntaxError，
  // 用户只看到 "Unexpected end of JSON input" 而拿不到任何可用信息。
  const raw = await resp.text()
  let body = null
  try {
    body = raw ? JSON.parse(raw) : null
  } catch {
    body = null
  }
  if (!body) throw new Error(`服务异常（HTTP ${resp.status}），请稍后重试`)

  if (body.code !== 0) {
    // 登录态失效：清掉本地凭证并回到登录页，否则用户会卡在原地反复报错
    if (resp.status === 401 || body.code === 401) {
      const had = !!getToken()
      clearToken()
      localStorage.removeItem(USER_KEY)
      if (had) location.reload()
      throw new Error(body.msg || '登录已失效，请重新登录')
    }
    throw new Error(body.msg || '请求失败')
  }
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
 *
 * resume：用户点「确认 / 取消」按钮时回传的结构化载荷，
 *         形如 { confirm: true, intent_id: 'a1b2c3d4e5f6' }；此时 message 传空串即可。
 *         走这条路径时后端不做自然语言解析，因此不会再出现
 *         「好的，但我想改时间」被误判为确认下单的情况。
 */
export function chatStream(message, token, onEvent, onDone, resume = null) {
  const controller = new AbortController()

  fetch('/py-api/api/chat', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      Authorization: `Bearer ${token}`
    },
    body: JSON.stringify(resume ? { message: message || '', resume } : { message }),
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
  // 走业务侧只读接口；/api/rail/stations 是 ADMIN-only，普通用户会 403
  return jfetch(`/java-api/api/ticket/stations${kw ? `?kw=${encodeURIComponent(kw)}` : ''}`)
}

export async function queryTickets(from, to, date, seatClass = '') {
  const p = new URLSearchParams({ from, to, date })
  if (seatClass) p.set('seatClass', seatClass)
  return jfetch(`/java-api/api/ticket/query?${p.toString()}`)
}

export async function buyTicket({ tripId, seatClass, fromStationId, toStationId, contactId = null, ticketType = 'ADULT' }) {
  return jfetch('/java-api/api/ticket/buy', {
    method: 'POST',
    body: JSON.stringify({ tripId, seatClass, fromStationId, toStationId, contactId, ticketType })
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

/** 知识库操作审计（写操作 + 被拒的越权尝试），最新在前 */
export async function kbAudit(limit = 100) {
  return jfetch(`/py-api/api/admin/kb/audit?limit=${limit}`)
}

// ---------- 铁路管理（仅 ADMIN） ----------

/** 高危操作一次性确认码：删除类接口要求先取码，再以 X-Confirm-Token 头携带。 */
async function railConfirm(op, target) {
  return jfetch('/java-api/api/rail/confirm/request', {
    method: 'POST',
    body: JSON.stringify({ op, target })
  })
}

export async function railStations(kw = '') {
  return jfetch(`/java-api/api/rail/stations${kw ? `?kw=${encodeURIComponent(kw)}` : ''}`)
}

export async function railAddStation(code, name) {
  return jfetch('/java-api/api/rail/stations', { method: 'POST', body: JSON.stringify({ code, name }) })
}

export async function railTrains() {
  return jfetch('/java-api/api/rail/trains')
}

export async function railAddTrain(code, name, kind) {
  return jfetch('/java-api/api/rail/trains', { method: 'POST', body: JSON.stringify({ code, name, kind }) })
}

export async function railUpdateTrain(id, name, kind) {
  return jfetch(`/java-api/api/rail/trains/${id}`, { method: 'PUT', body: JSON.stringify({ name, kind }) })
}

export async function railDeleteTrain(id) {
  const c = await railConfirm('deleteTrain', id)
  return jfetch(`/java-api/api/rail/trains/${id}`, { method: 'DELETE', headers: { 'X-Confirm-Token': c.token } })
}

export async function railTrainStops(trainId) {
  return jfetch(`/java-api/api/rail/trains/${trainId}/stops`)
}

/** 整体重建经停序列（按数组顺序赋 seq，至少两个站）。 */
export async function railSaveStops(trainId, stops) {
  return jfetch(`/java-api/api/rail/trains/${trainId}/stops`, {
    method: 'POST',
    body: JSON.stringify({ stops })
  })
}

export async function railTrips({ date = '', trainId = '' } = {}) {
  const p = new URLSearchParams()
  if (date) p.set('date', date)
  if (trainId) p.set('trainId', trainId)
  const q = p.toString()
  return jfetch(`/java-api/api/rail/trips${q ? `?${q}` : ''}`)
}

export async function railTripDetail(id) {
  return jfetch(`/java-api/api/rail/trips/${id}`)
}

export async function railCreateTrip(trainId, travelDate, classes) {
  return jfetch('/java-api/api/rail/trips', {
    method: 'POST',
    body: JSON.stringify({ trainId, travelDate, classes })
  })
}

export async function railDeleteTrip(id) {
  const c = await railConfirm('deleteTrip', id)
  return jfetch(`/java-api/api/rail/trips/${id}`, { method: 'DELETE', headers: { 'X-Confirm-Token': c.token } })
}
