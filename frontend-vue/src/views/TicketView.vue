<script setup>
import { onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { buyTicket, listContacts, listStations, payOrder, queryTickets } from '../api'

const props = defineProps({ user: { type: Object, required: true } })

const stations = ref([]) // {id,name}
const contacts = ref([])
const rows = ref([])
const loading = ref(false)

const form = reactive({
  from: null,
  to: null,
  date: new Date(Date.now() + 86400000).toISOString().slice(0, 10),
  seatClass: ''
})

onMounted(async () => {
  try {
    stations.value = await listStations()
    contacts.value = await listContacts()
  } catch (e) {
    ElMessage.error(e.message)
  }
})

const yuan = (c) => (c / 100).toFixed(0)

async function search() {
  if (!form.from || !form.to || !form.date) {
    ElMessage.warning('请选择出发地、到达地与日期')
    return
  }
  loading.value = true
  try {
    rows.value = await queryTickets(form.from, form.to, form.date, form.seatClass)
    if (!rows.value.length) ElMessage.info('该区间当日无可用车次或余票')
  } catch (e) {
    ElMessage.error(e.message)
  } finally {
    loading.value = false
  }
}

// 购票弹窗：可选乘车人（常用联系人 / 本人）与票种（成人 / 儿童 / 学生）。
// 儿童票、学生票的资格与价格由 Java 侧 TicketPolicy 确定性裁决，前端只负责传票种，不做判断。
const buyVisible = ref(false)
const buying = ref(false)
const buyRow = ref(null)
const buyForm = reactive({ contactId: null, ticketType: 'ADULT' })
const TICKET_TYPES = [
  { value: 'ADULT', label: '成人票' },
  { value: 'CHILD', label: '儿童票（6-14 周岁半价）' },
  { value: 'STUDENT', label: '学生票（学生 + 二等座 + 寒暑假）' }
]

function openBuy(row) {
  buyRow.value = row
  buyForm.contactId = null
  buyForm.ticketType = 'ADULT'
  buyVisible.value = true
}

async function confirmBuy() {
  if (!buyRow.value) return
  buying.value = true
  try {
    const r = await buyTicket({
      tripId: buyRow.value.tripId,
      seatClass: buyRow.value.seatClass,
      fromStationId: form.from,
      toStationId: form.to,
      contactId: buyForm.contactId,
      ticketType: buyForm.ticketType
    })
    buyVisible.value = false
    ElMessage.success(`下单成功，订单号 ${r.orderNo}（待支付）`)
    await goPay(r.requestId)
    await search()
  } catch (e) {
    ElMessage.error(e.message || '下单失败')
  } finally {
    buying.value = false
  }
}

async function goPay(requestId) {
  try {
    await ElMessageBox.confirm('座位已锁定，立即支付？', '支付', {
      confirmButtonText: '立即支付', cancelButtonText: '稍后在我的车票支付'
    })
    await payOrder(requestId)
    ElMessage.success('支付成功')
  } catch (_) {
    /* 用户选择稍后支付 */
  }
}
</script>

<template>
  <div class="panel">
    <el-form inline>
      <el-form-item label="出发地">
        <el-select v-model="form.from" filterable placeholder="出发站" style="width: 150px">
          <el-option v-for="s in stations" :key="s.id" :label="s.name" :value="s.id" />
        </el-select>
      </el-form-item>
      <el-form-item label="到达地">
        <el-select v-model="form.to" filterable placeholder="到达站" style="width: 150px">
          <el-option v-for="s in stations" :key="s.id" :label="s.name" :value="s.id" />
        </el-select>
      </el-form-item>
      <el-form-item label="日期">
        <el-date-picker v-model="form.date" type="date" value-format="YYYY-MM-DD" placeholder="乘车日期" />
      </el-form-item>
      <el-form-item label="席别">
        <el-select v-model="form.seatClass" clearable placeholder="全部席别" style="width: 130px">
          <el-option label="二等座" value="二等座" />
          <el-option label="一等座" value="一等座" />
          <el-option label="商务座" value="商务座" />
        </el-select>
      </el-form-item>
      <el-form-item>
        <el-button type="primary" :loading="loading" @click="search">查询车票</el-button>
      </el-form-item>
    </el-form>

    <el-table :data="rows" stripe empty-text="请输入条件查询车票">
      <el-table-column prop="trainCode" label="车次" width="90" />
      <el-table-column label="区间" min-width="170">
        <template #default="{ row }">{{ row.from }} -> {{ row.to }}</template>
      </el-table-column>
      <el-table-column label="时间" width="170">
        <template #default="{ row }">{{ row.departTime }} ~ {{ row.arriveTime }}</template>
      </el-table-column>
      <el-table-column prop="seatClass" label="席别" width="90" />
      <el-table-column label="票价" width="90">
        <template #default="{ row }">¥{{ yuan(row.priceCents) }}</template>
      </el-table-column>
      <el-table-column label="余票" width="80">
        <template #default="{ row }">
          <span :class="row.remaining > 0 ? 'ok' : 'no'">{{ row.remaining }} 张</span>
        </template>
      </el-table-column>
      <el-table-column label="操作" width="90">
        <template #default="{ row }">
          <el-button size="small" type="primary" :disabled="row.remaining <= 0" @click="openBuy(row)">
            购票
          </el-button>
        </template>
      </el-table-column>
    </el-table>

    <el-dialog v-model="buyVisible" title="确认购票" width="420px">
      <div v-if="buyRow" class="buy-sum">
        {{ buyRow.trainCode }} {{ buyRow.from }} -> {{ buyRow.to }}　{{ buyRow.seatClass }}　¥{{ yuan(buyRow.priceCents) }}
      </div>
      <el-form label-width="80px" style="margin-top: 12px">
        <el-form-item label="乘车人">
          <el-select v-model="buyForm.contactId" clearable placeholder="本人" style="width: 100%">
            <el-option
              v-for="c in contacts"
              :key="c.id"
              :label="c.age ? `${c.name}（${c.age} 岁）` : c.name"
              :value="c.id"
            />
          </el-select>
        </el-form-item>
        <el-form-item label="票种">
          <el-select v-model="buyForm.ticketType" style="width: 100%">
            <el-option v-for="t in TICKET_TYPES" :key="t.value" :label="t.label" :value="t.value" />
          </el-select>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="buyVisible = false">取消</el-button>
        <el-button type="primary" :loading="buying" @click="confirmBuy">确认购买</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
.panel { background: #fff; border: 1px solid var(--resv-line); padding: 16px; }
.ok { color: #1e4e8c; }
.no { color: #b0b8c2; }
.buy-sum { color: #5f7083; font-size: 13px; }
</style>
