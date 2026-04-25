<script setup lang="ts">
/**
 * 指数成分历史浏览器：HS300 / ZZ500 / ZZ1000 当前成分 + 历次调整时间轴
 * + 单股进出历史。
 *
 * 接口：
 * - /api/indices/list
 * - /api/indices/current?index_code=
 * - /api/indices/adjustments?index_code=
 * - /api/indices/symbol_membership?symbol=
 */
import { computed, h, onMounted, ref, watch } from 'vue'
import {
  NPageHeader, NSpace, NCard, NSelect, NDataTable, NTag, NTabs, NTabPane,
  NEmpty, NSpin, NTimeline, NTimelineItem, NAlert, NDrawer, NDrawerContent,
  NButton,
} from 'naive-ui'
import type { DataTableColumns, SelectOption } from 'naive-ui'
import { client } from '@/api/client'

interface IndexRow {
  index_code: string
  active: number
  adjustments: number
  first_adjustment: string | null
  last_adjustment: string | null
}
interface CurrentItem {
  symbol: string
  name: string
  industry_l1: string
  effective_date: string | null
}
interface AdjustEvent {
  date: string
  entries: { symbol: string; name: string }[]
  departures: { symbol: string; name: string }[]
}
interface MembershipRow {
  index_code: string
  effective_date: string | null
  end_date: string | null
}

const indices = ref<IndexRow[]>([])
const indicesLoading = ref(false)
const selectedIndex = ref<string | null>(null)
const currentItems = ref<CurrentItem[]>([])
const events = ref<AdjustEvent[]>([])
const detailLoading = ref(false)
const errMsg = ref('')

// 单股 drilldown
const drawerOpen = ref(false)
const drawerSymbol = ref('')
const drawerName = ref('')
const drawerMemberships = ref<MembershipRow[]>([])
const drawerLoading = ref(false)

// 行业 filter
const industryFilter = ref<string | null>(null)
const industryOptions = computed<SelectOption[]>(() => {
  const set = new Set(currentItems.value.map(it => it.industry_l1))
  return [{ label: '全部行业', value: null as any },
    ...Array.from(set).sort().map(v => ({ label: v, value: v }))]
})

const filteredCurrent = computed(() => {
  if (!industryFilter.value) return currentItems.value
  return currentItems.value.filter(it => it.industry_l1 === industryFilter.value)
})

// ---------- Index 选择项 ----------

const INDEX_NAME_MAP: Record<string, string> = {
  '000300.SH': '沪深 300',
  '000905.SH': '中证 500',
  '000852.SH': '中证 1000',
}

const indexOptions = computed<SelectOption[]>(() =>
  indices.value.map(r => ({
    label: `${INDEX_NAME_MAP[r.index_code] || r.index_code}（${r.index_code} · 当前 ${r.active}）`,
    value: r.index_code,
  })))

// ---------- Lifecycle ----------

async function loadIndices() {
  indicesLoading.value = true
  errMsg.value = ''
  try {
    const r = await client.get('/indices/list')
    indices.value = r.data
    if (!selectedIndex.value && indices.value.length) {
      selectedIndex.value = indices.value[0].index_code
    }
  } catch (e: any) {
    errMsg.value = e?.message || '加载指数列表失败'
  } finally {
    indicesLoading.value = false
  }
}

async function loadDetail(code: string) {
  detailLoading.value = true
  try {
    const [c, a] = await Promise.all([
      client.get('/indices/current', { params: { index_code: code } }),
      client.get('/indices/adjustments', { params: { index_code: code } }),
    ])
    currentItems.value = c.data.items
    events.value = a.data.events
    industryFilter.value = null
  } catch (e: any) {
    errMsg.value = e?.message || '加载指数详情失败'
  } finally {
    detailLoading.value = false
  }
}

watch(selectedIndex, (v) => { if (v) loadDetail(v) })
onMounted(loadIndices)

// ---------- 单股 drilldown ----------

async function openSymbol(symbol: string, name: string) {
  drawerSymbol.value = symbol
  drawerName.value = name
  drawerOpen.value = true
  drawerLoading.value = true
  drawerMemberships.value = []
  try {
    const r = await client.get('/indices/symbol_membership', { params: { symbol } })
    drawerName.value = r.data.name || name
    drawerMemberships.value = r.data.memberships
  } catch (e: any) {
    errMsg.value = e?.message || '加载个股进出历史失败'
  } finally {
    drawerLoading.value = false
  }
}

// ---------- Tables ----------

const currentCols: DataTableColumns<CurrentItem> = [
  {
    title: '股票',
    key: 'symbol',
    width: 180,
    render: (r) => h('a', {
      style: 'color:#F0B90B; cursor:pointer',
      onClick: () => openSymbol(r.symbol, r.name),
    }, [r.symbol, ' ', h('span', { style: 'color:#888' }, r.name)]),
  },
  { title: '行业', key: 'industry_l1' },
  { title: '入选日', key: 'effective_date', width: 140 },
]

// ---------- Adjustments 时间轴：倒序最新在前 ----------

const eventsDesc = computed(() => [...events.value].reverse())
</script>

<template>
  <div>
    <n-page-header title="指数成分历史" style="margin-bottom: 16px">
      <template #subtitle>
        HS300 / ZZ500 / ZZ1000 当前成分 + 历次调整 + 单股被收录历史
      </template>
    </n-page-header>

    <n-alert v-if="errMsg" type="error" :show-icon="false" style="margin-bottom: 12px">
      {{ errMsg }}
    </n-alert>

    <n-space vertical :size="16">
      <!-- 顶栏：选指数 -->
      <n-card size="small">
        <n-space align="center" :size="12">
          <span style="color: #888">指数</span>
          <n-select
            v-model:value="selectedIndex"
            :options="indexOptions"
            :loading="indicesLoading"
            style="width: 360px"
            placeholder="加载中..."
          />
          <n-button :loading="detailLoading" @click="selectedIndex && loadDetail(selectedIndex)">
            刷新
          </n-button>
        </n-space>
      </n-card>

      <n-tabs type="line" animated>
        <n-tab-pane name="current" tab="当前成分">
          <n-card size="small">
            <n-spin :show="detailLoading">
              <n-space justify="space-between" style="margin-bottom: 8px">
                <n-space align="center" :size="8">
                  <span style="color: #888">行业筛选:</span>
                  <n-select
                    v-model:value="industryFilter"
                    :options="industryOptions"
                    style="width: 280px"
                    clearable
                  />
                  <span style="color: #888">
                    显示 {{ filteredCurrent.length }} / {{ currentItems.length }}
                  </span>
                </n-space>
              </n-space>
              <n-data-table
                :columns="currentCols"
                :data="filteredCurrent"
                :bordered="false"
                :single-line="false"
                size="small"
                :pagination="{ pageSize: 30 }"
              />
              <n-empty v-if="!currentItems.length && !detailLoading" description="暂无成分数据" />
            </n-spin>
          </n-card>
        </n-tab-pane>

        <n-tab-pane name="timeline" tab="调整时间轴">
          <n-card size="small">
            <n-spin :show="detailLoading">
              <n-alert type="info" :show-icon="false" style="margin-bottom: 12px">
                每次调整日的进入 / 离开名单。最新一次在最上方。
              </n-alert>
              <n-empty v-if="!events.length && !detailLoading" description="暂无调整记录" />
              <n-timeline v-else>
                <n-timeline-item
                  v-for="ev in eventsDesc"
                  :key="ev.date"
                  type="info"
                  :title="ev.date"
                >
                  <n-space vertical :size="6">
                    <div v-if="ev.entries.length">
                      <n-tag size="small" type="success" style="margin-right: 6px">
                        新进 {{ ev.entries.length }}
                      </n-tag>
                      <span style="font-size: 12px">
                        <a v-for="(e, i) in ev.entries" :key="e.symbol"
                           style="color:#F0B90B; cursor:pointer; margin-right: 6px"
                           @click="openSymbol(e.symbol, e.name)">
                          {{ e.symbol }}<span style="color:#888"> {{ e.name }}</span>{{ i < ev.entries.length - 1 ? ',' : '' }}
                        </a>
                      </span>
                    </div>
                    <div v-if="ev.departures.length">
                      <n-tag size="small" type="error" style="margin-right: 6px">
                        踢出 {{ ev.departures.length }}
                      </n-tag>
                      <span style="font-size: 12px">
                        <a v-for="(e, i) in ev.departures" :key="e.symbol"
                           style="color:#888; cursor:pointer; margin-right: 6px"
                           @click="openSymbol(e.symbol, e.name)">
                          {{ e.symbol }}<span style="color:#666"> {{ e.name }}</span>{{ i < ev.departures.length - 1 ? ',' : '' }}
                        </a>
                      </span>
                    </div>
                  </n-space>
                </n-timeline-item>
              </n-timeline>
            </n-spin>
          </n-card>
        </n-tab-pane>
      </n-tabs>
    </n-space>

    <!-- 单股 drilldown drawer -->
    <n-drawer v-model:show="drawerOpen" :width="520">
      <n-drawer-content :title="`${drawerSymbol} ${drawerName}`" closable>
        <n-spin :show="drawerLoading">
          <n-alert type="info" :show-icon="false" style="margin-bottom: 12px">
            该股票在各指数中的进出区间。<b>end_date 为空</b>表示当前仍在该指数。
          </n-alert>
          <n-empty v-if="!drawerMemberships.length && !drawerLoading"
                   description="该股票未被任何 (HS300/ZZ500/ZZ1000) 指数收录" />
          <n-timeline v-else>
            <n-timeline-item
              v-for="(m, i) in drawerMemberships" :key="i"
              :type="m.end_date ? 'default' : 'success'"
              :title="`${INDEX_NAME_MAP[m.index_code] || m.index_code} (${m.index_code})`"
            >
              <span style="font-size: 13px">
                {{ m.effective_date }} ~ {{ m.end_date || '至今' }}
              </span>
            </n-timeline-item>
          </n-timeline>
        </n-spin>
      </n-drawer-content>
    </n-drawer>
  </div>
</template>
