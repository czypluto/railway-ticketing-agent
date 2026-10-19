<script setup>
// 铁路管理（仅 ADMIN）：车站 / 列车（含经停序列）/ 车次运行日与席别。
// 删除列车、删除车次属高危操作：前端先取一次性确认码，再随 X-Confirm-Token 头提交，
// 后端会校验并一次性消费掉该码（2 分钟有效，验过即废）。
import { onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  localDate,
  railAddStation, railAddTrain, railCreateTrip, railDeleteTrain, railDeleteTrip,
  railSaveStops, railStations, railTrainStops, railTrains, railTripDetail, railTrips, railUpdateTrain
} from '../api'

const SEAT_CLASSES = ['二等座', '一等座', '商务座']
const KINDS = ['G', 'D', 'C', 'Z', 'T', 'K']

const tab = ref('stations')

/* ---------------- 车站 ---------------- */
const stations = ref([])
const stationLoading = ref(false)
const stationForm = reactive({ code: '', name: '' })

async function loadStations() {
  stationLoading.value = true
  try {
    stations.value = await railStations()
  } catch (e) {
    ElMessage.error(e.message)
  } finally {
    stationLoading.value = false
  }
}

async function addStation() {
  if (!stationForm.name) return ElMessage.warning('请填写站名')
  try {
    await railAddStation(stationForm.code || stationForm.name, stationForm.name)
    ElMessage.success('已新增车站')
    stationForm.code = ''
    stationForm.name = ''
    await loadStations()
  } catch (e) {
    ElMessage.error(e.message)
  }
}

/* ---------------- 列车 ---------------- */
const trains = ref([])
const trainLoading = ref(false)
const trainDialog = ref(false)
const trainEditing = ref(null)
const trainForm = reactive({ code: '', name: '', kind: 'G' })

async function loadTrains() {
  trainLoading.value = true
  try {
    trains.value = await railTrains()
  } catch (e) {
    ElMessage.error(e.message)
  } finally {
    trainLoading.value = false
  }
}

function openTrainDialog(row) {
  trainEditing.value = row || null
  trainForm.code = row ? row.code : ''
  trainForm.name = row ? row.name || '' : ''
  trainForm.kind = row ? row.kind || 'G' : 'G'
  trainDialog.value = true
}

async function saveTrain() {
  try {
    if (trainEditing.value) {
      await railUpdateTrain(trainEditing.value.id, trainForm.name, trainForm.kind)
      ElMessage.success('已更新车次')
    } else {
      if (!trainForm.code) return ElMessage.warning('请填写车次号')
      await railAddTrain(trainForm.code, trainForm.name, trainForm.kind)
      ElMessage.success('已新增车次')
    }
    trainDialog.value = false
    await loadTrains()
  } catch (e) {
    ElMessage.error(e.message)
  }
}

async function removeTrain(row) {
  try {
    await ElMessageBox.confirm(
      `确定删除车次「${row.code}」？该操作需一次性确认码，且已有运行记录时会被拒绝。`,
      '高危操作确认',
      { type: 'warning', confirmButtonText: '删除', cancelButtonText: '取消' }
    )
  } catch {
    return
  }
  try {
    await railDeleteTrain(row.id)
    ElMessage.success('已删除')
    await loadTrains()
  } catch (e) {
    ElMessage.error(e.message)
  }
}

/* ---------------- 经停序列 ---------------- */
const stopsDialog = ref(false)
const stopsTrain = ref(null)
const stops = ref([])
const stopsSaving = ref(false)

async function openStops(row) {
  stopsTrain.value = row
  stops.value = []
  try {
    const list = await railTrainStops(row.id)
    stops.value = list.map((s) => ({
      stationId: s.stationId,
      arrive: s.arrive || '',
      depart: s.depart || ''
    }))
    if (!stops.value.length) addStopRow()
  } catch (e) {
    ElMessage.error(e.message)
  }
  stopsDialog.value = true
}

function addStopRow() {
  stops.value.push({ stationId: null, arrive: '', depart: '' })
}

function removeStopRow(i) {
  stops.value.splice(i, 1)
}

async function saveStops() {
  if (stops.value.length < 2) return ElMessage.warning('经停序列至少需要两个站')
  const rows = stops.value.filter((s) => s.stationId)
  if (rows.length !== stops.value.length) return ElMessage.warning('有未选择车站的行')
  stopsSaving.value = true
  try {
    await railSaveStops(stopsTrain.value.id, rows.map((s) => ({
      stationId: s.stationId,
      arrive: s.arrive || null,
      depart: s.depart || null
    })))
    ElMessage.success('经停序列已保存')
    stopsDialog.value = false
  } catch (e) {
    ElMessage.error(e.message)
  } finally {
    stopsSaving.value = false
  }
}

/* ---------------- 车次运行日 ---------------- */
const trips = ref([])
const tripLoading = ref(false)
const tripFilter = reactive({ trainId: '', date: '' })
const tripDialog = ref(false)
const tripForm = reactive({ trainId: '', travelDate: '', classes: [] })
const tripDetailVisible = ref(false)
const tripDetail = ref(null)

async function loadTrips() {
  tripLoading.value = true
  try {
    trips.value = await railTrips({ trainId: tripFilter.trainId, date: tripFilter.date })
  } catch (e) {
    ElMessage.error(e.message)
  } finally {
    tripLoading.value = false
  }
}

function openTripDialog() {
  tripForm.trainId = ''
  tripForm.travelDate = localDate(1)
  tripForm.classes = [{ seatClass: '二等座', yuan: 553, totalSeats: 600 }]
  tripDialog.value = true
}

function addClassRow() {
  tripForm.classes.push({ seatClass: '一等座', yuan: 933, totalSeats: 100 })
}

function removeClassRow(i) {
  tripForm.classes.splice(i, 1)
}

async function saveTrip() {
  if (!tripForm.trainId || !tripForm.travelDate) return ElMessage.warning('请选择车次与日期')
  const classes = tripForm.classes
    .filter((c) => c.seatClass && c.totalSeats)
    .map((c) => ({
      seatClass: c.seatClass,
      priceCents: Math.round(Number(c.yuan) * 100),
      totalSeats: Number(c.totalSeats)
    }))
  if (!classes.length) return ElMessage.warning('至少一个席别')
  try {
    await railCreateTrip(tripForm.trainId, tripForm.travelDate, classes)
    ElMessage.success('已新增运行日')
    tripDialog.value = false
    await loadTrips()
  } catch (e) {
    ElMessage.error(e.message)
  }
}

async function openTripDetail(row) {
  try {
    tripDetail.value = await railTripDetail(row.id)
    tripDetailVisible.value = true
  } catch (e) {
    ElMessage.error(e.message)
  }
}

async function removeTrip(row) {
  try {
    await ElMessageBox.confirm(
      `确定删除车次运行日「${row.trainCode} ${row.travelDate}」？会同时删除其席别配置，需一次性确认码。`,
      '高危操作确认',
      { type: 'warning', confirmButtonText: '删除', cancelButtonText: '取消' }
    )
  } catch {
    return
  }
  try {
    await railDeleteTrip(row.id)
    ElMessage.success('已删除')
    await loadTrips()
  } catch (e) {
    ElMessage.error(e.message)
  }
}

function yuan(c) {
  return ((c || 0) / 100).toFixed(2)
}

onMounted(async () => {
  await loadStations()
  await loadTrains()
  await loadTrips()
})
</script>

<template>
  <div class="rail">
    <div class="rail-bar">
      <span class="rail-title">铁路管理</span>
      <span class="rail-hint">仅管理员可访问；删除列车 / 车次需一次性确认码（2 分钟有效，验过即废）</span>
    </div>

    <el-tabs v-model="tab">
      <!-- 车站 -->
      <el-tab-pane label="车站" name="stations">
        <div class="rail-tools">
          <el-input v-model="stationForm.code" placeholder="编码（如 BJP，留空取站名）" style="width: 220px" />
          <el-input v-model="stationForm.name" placeholder="站名（如 北京南）" style="width: 180px" />
          <el-button type="primary" @click="addStation">新增车站</el-button>
          <el-button @click="loadStations">刷新</el-button>
        </div>
        <el-table :data="stations" v-loading="stationLoading" size="small" border>
          <el-table-column prop="id" label="ID" width="80" />
          <el-table-column prop="code" label="编码" width="140" />
          <el-table-column prop="name" label="站名" min-width="200" />
        </el-table>
      </el-tab-pane>

      <!-- 列车 -->
      <el-tab-pane label="列车" name="trains">
        <div class="rail-tools">
          <el-button type="primary" @click="openTrainDialog(null)">新增列车</el-button>
          <el-button @click="loadTrains">刷新</el-button>
        </div>
        <el-table :data="trains" v-loading="trainLoading" size="small" border>
          <el-table-column prop="id" label="ID" width="80" />
          <el-table-column prop="code" label="车次号" width="120" />
          <el-table-column prop="name" label="名称" min-width="160" />
          <el-table-column prop="kind" label="类型" width="80" />
          <el-table-column label="操作" width="240">
            <template #default="{ row }">
              <el-button size="small" text @click="openStops(row)">经停序列</el-button>
              <el-button size="small" text @click="openTrainDialog(row)">编辑</el-button>
              <el-button size="small" text type="danger" @click="removeTrain(row)">删除</el-button>
            </template>
          </el-table-column>
        </el-table>
      </el-tab-pane>

      <!-- 车次运行日 -->
      <el-tab-pane label="车次运行日" name="trips">
        <div class="rail-tools">
          <el-select v-model="tripFilter.trainId" clearable placeholder="按列车筛选" style="width: 200px">
            <el-option v-for="t in trains" :key="t.id" :label="`${t.code}${t.name ? ' ' + t.name : ''}`" :value="t.id" />
          </el-select>
          <el-date-picker v-model="tripFilter.date" type="date" value-format="YYYY-MM-DD" placeholder="按日期筛选" />
          <el-button @click="loadTrips">查询</el-button>
          <el-button type="primary" @click="openTripDialog">新增运行日</el-button>
        </div>
        <el-table :data="trips" v-loading="tripLoading" size="small" border>
          <el-table-column prop="id" label="ID" width="80" />
          <el-table-column prop="trainCode" label="车次" width="120" />
          <el-table-column prop="travelDate" label="开行日期" width="140" />
          <el-table-column prop="status" label="状态" width="100" />
          <el-table-column label="操作" width="200">
            <template #default="{ row }">
              <el-button size="small" text @click="openTripDetail(row)">详情</el-button>
              <el-button size="small" text type="danger" @click="removeTrip(row)">删除</el-button>
            </template>
          </el-table-column>
          <template #empty>
            <div class="rail-empty">无可显示的车次运行日。</div>
          </template>
        </el-table>
      </el-tab-pane>
    </el-tabs>

    <!-- 新增/编辑列车 -->
    <el-dialog v-model="trainDialog" :title="trainEditing ? '编辑列车' : '新增列车'" width="420px">
      <el-form label-width="80px">
        <el-form-item label="车次号">
          <el-input v-model="trainForm.code" :disabled="!!trainEditing" placeholder="如 G101" />
        </el-form-item>
        <el-form-item label="名称">
          <el-input v-model="trainForm.name" placeholder="如 京沪高速" />
        </el-form-item>
        <el-form-item label="类型">
          <el-select v-model="trainForm.kind" style="width: 100%">
            <el-option v-for="k in KINDS" :key="k" :label="k" :value="k" />
          </el-select>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="trainDialog = false">取消</el-button>
        <el-button type="primary" @click="saveTrain">保存</el-button>
      </template>
    </el-dialog>

    <!-- 经停序列 -->
    <el-dialog v-model="stopsDialog" :title="`经停序列 · ${stopsTrain?.code || ''}`" width="620px">
      <div class="rail-hint" style="margin-bottom: 8px">
        按列表顺序赋 seq，至少两个站；首站到达时间可留空。保存会整体重建该列车的经停。
      </div>
      <el-table :data="stops" size="small" border>
        <el-table-column label="#" width="50">
          <template #default="{ $index }">{{ $index + 1 }}</template>
        </el-table-column>
        <el-table-column label="车站" min-width="200">
          <template #default="{ row }">
            <el-select v-model="row.stationId" filterable placeholder="选择车站" style="width: 100%">
              <el-option v-for="s in stations" :key="s.id" :label="s.name" :value="s.id" />
            </el-select>
          </template>
        </el-table-column>
        <el-table-column label="到达" width="120">
          <template #default="{ row }">
            <el-input v-model="row.arrive" placeholder="HH:mm" />
          </template>
        </el-table-column>
        <el-table-column label="发车" width="120">
          <template #default="{ row }">
            <el-input v-model="row.depart" placeholder="HH:mm" />
          </template>
        </el-table-column>
        <el-table-column label="操作" width="70">
          <template #default="{ $index }">
            <el-button size="small" text type="danger" @click="removeStopRow($index)">删</el-button>
          </template>
        </el-table-column>
      </el-table>
      <el-button size="small" style="margin-top: 8px" @click="addStopRow">添加停靠站</el-button>
      <template #footer>
        <el-button @click="stopsDialog = false">取消</el-button>
        <el-button type="primary" :loading="stopsSaving" @click="saveStops">保存</el-button>
      </template>
    </el-dialog>

    <!-- 新增运行日 -->
    <el-dialog v-model="tripDialog" title="新增车次运行日" width="620px">
      <el-form label-width="80px">
        <el-form-item label="列车">
          <el-select v-model="tripForm.trainId" filterable placeholder="选择列车" style="width: 100%">
            <el-option v-for="t in trains" :key="t.id" :label="`${t.code}${t.name ? ' ' + t.name : ''}`" :value="t.id" />
          </el-select>
        </el-form-item>
        <el-form-item label="开行日期">
          <el-date-picker v-model="tripForm.travelDate" type="date" value-format="YYYY-MM-DD" style="width: 100%" />
        </el-form-item>
        <el-form-item label="席别">
          <div style="width: 100%">
            <div v-for="(c, i) in tripForm.classes" :key="i" class="class-row">
              <el-select v-model="c.seatClass" style="width: 130px">
                <el-option v-for="s in SEAT_CLASSES" :key="s" :label="s" :value="s" />
              </el-select>
              <el-input v-model="c.yuan" placeholder="票价（元）" style="width: 130px" />
              <el-input v-model="c.totalSeats" placeholder="总席位" style="width: 120px" />
              <el-button size="small" text type="danger" @click="removeClassRow(i)">删</el-button>
            </div>
            <el-button size="small" @click="addClassRow">添加席别</el-button>
          </div>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="tripDialog = false">取消</el-button>
        <el-button type="primary" @click="saveTrip">保存</el-button>
      </template>
    </el-dialog>

    <!-- 运行日详情 -->
    <el-dialog v-model="tripDetailVisible" title="运行日详情" width="560px">
      <template v-if="tripDetail">
        <div class="rail-hint" style="margin-bottom: 8px">
          {{ tripDetail.trainCode }} · {{ tripDetail.travelDate }}
        </div>
        <div class="detail-label">席别与库存</div>
        <el-table :data="tripDetail.classes" size="small" border>
          <el-table-column prop="seatClass" label="席别" />
          <el-table-column label="票价"><template #default="{ row }">¥{{ yuan(row.priceCents) }}</template></el-table-column>
          <el-table-column prop="totalSeats" label="总席位" />
        </el-table>
        <div class="detail-label">经停序列</div>
        <el-table :data="tripDetail.stops" size="small" border>
          <el-table-column prop="seq" label="序" width="60" />
          <el-table-column prop="station" label="车站" />
          <el-table-column prop="arrive" label="到达" width="100" />
          <el-table-column prop="depart" label="发车" width="100" />
        </el-table>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
.rail {
  background: #fff;
  border: 1px solid var(--resv-line);
  padding: 16px;
}
.rail-bar {
  display: flex;
  align-items: baseline;
  gap: 12px;
  margin-bottom: 8px;
}
.rail-title {
  color: var(--resv-blue-dark);
  font-size: 16px;
  font-weight: 600;
}
.rail-hint {
  color: #5f7083;
  font-size: 12px;
}
.rail-tools {
  display: flex;
  gap: 8px;
  margin-bottom: 12px;
  flex-wrap: wrap;
}
.rail-empty {
  color: #9aa7b5;
  padding: 20px 0;
}
.class-row {
  display: flex;
  gap: 8px;
  margin-bottom: 6px;
}
.detail-label {
  margin: 12px 0 6px;
  color: var(--resv-blue-dark);
  font-size: 13px;
  font-weight: 600;
}
</style>
