<template>
  <div class="result-container">
    <div class="page-header">
      <a-button class="back-button" size="large" @click="goBack">
        ← 返回首页
      </a-button>
      <a-space size="middle">
        <a-button v-if="!editMode" @click="toggleEditMode" type="default">
          ✏️ 编辑行程
        </a-button>
        <a-button v-else @click="saveChanges" type="primary">
          💾 保存修改
        </a-button>
        <a-button v-if="editMode" @click="cancelEdit" type="default">
          ❌ 取消编辑
        </a-button>

        <a-dropdown v-if="!editMode">
          <template #overlay>
            <a-menu>
              <a-menu-item key="image" @click="exportAsImage">
                📷 导出为图片
              </a-menu-item>
              <a-menu-item key="pdf" @click="exportAsPDF">
                📄 导出为PDF
              </a-menu-item>
            </a-menu>
          </template>
          <a-button type="default">
            📥 导出行程 <DownOutlined />
          </a-button>
        </a-dropdown>
      </a-space>
    </div>

    <a-spin :spinning="isRefining" tip="AI 正在根据您的要求重新推演行程，请稍候...">
      <div v-if="tripPlan" class="content-wrapper">
        <div class="side-nav">
          <a-affix :offset-top="80">
            <a-menu mode="inline" :selected-keys="[activeSection]" @click="scrollToSection">
              <a-menu-item key="overview">
                <span>📋 行程概览</span>
              </a-menu-item>
              <a-menu-item key="budget" v-if="tripPlan.budget">
                <span>💰 预算明细</span>
              </a-menu-item>
              <a-menu-item key="map">
                <span>📍 景点地图</span>
              </a-menu-item>
              <a-sub-menu key="days" title="📅 每日行程">
                <a-menu-item v-for="(day, index) in tripPlan.days" :key="`day-${index}`">
                  第{{ day.day_index + 1 }}天
                </a-menu-item>
              </a-sub-menu>
              <a-menu-item key="weather" v-if="tripPlan.weather_info && tripPlan.weather_info.length > 0">
                <span>🌤️ 天气信息</span>
              </a-menu-item>
            </a-menu>
          </a-affix>
        </div>

        <div class="main-content">

          <a-card v-if="criticScores" title="🕵️‍♂️ AI 架构师质检报告" class="critic-card" :bordered="false" style="margin-bottom: 20px;">
            <a-row :gutter="16">
              <a-col :span="8">
                <div class="score-label">地理合理性</div>
                <a-rate disabled :value="criticScores.geo_score / 2" allow-half />
                <span class="score-text">{{ criticScores.geo_score }}分</span>
              </a-col>
              <a-col :span="8">
                <div class="score-label">常识与预算匹配</div>
                <a-rate disabled :value="criticScores.budget_score / 2" allow-half />
                <span class="score-text">{{ criticScores.budget_score }}分</span>
              </a-col>
              <a-col :span="8">
                <div class="score-label">偏好满足度</div>
                <a-rate disabled :value="criticScores.preference_score / 2" allow-half />
                <span class="score-text">{{ criticScores.preference_score }}分</span>
              </a-col>
            </a-row>
            <div class="critique-text">
              <strong>AI 综合评语：</strong>{{ criticScores.critique }}
            </div>
            <div class="tier-tag">
              <a-tag color="blue">当前消费层级: {{ consumptionTier || '标准' }}</a-tag>
            </div>
          </a-card>

          <div class="top-info-section">
            <div class="left-info">
              <a-card id="overview" :title="`${tripPlan.city}旅行计划`" :bordered="false" class="overview-card">
                <div class="overview-content">
                  <div class="info-item">
                    <span class="info-label">📅 日期:</span>
                    <span class="info-value">{{ tripPlan.start_date }} 至 {{ tripPlan.end_date }}</span>
                  </div>
                  <div class="info-item">
                    <span class="info-label">💡 建议:</span>
                    <span class="info-value">{{ tripPlan.overall_suggestions }}</span>
                  </div>
                  <div class="info-item" v-if="tripPlan.exclusive_tips">
                    <span class="info-label" style="color: #1890ff;">🌟 本地避坑锦囊:</span>
                    <span class="info-value" style="font-weight: 500;">{{ tripPlan.exclusive_tips }}</span>
                  </div>
                </div>
              </a-card>

              <a-card id="budget" v-if="tripPlan.budget" title="💰 预算明细" :bordered="false" class="budget-card">
                <div class="budget-grid">
                  <div class="budget-item">
                    <div class="budget-label">景点门票</div>
                    <div class="budget-value">¥{{ tripPlan.budget.total_attractions }}</div>
                  </div>
                  <div class="budget-item">
                    <div class="budget-label">酒店住宿</div>
                    <div class="budget-value">¥{{ tripPlan.budget.total_hotels }}</div>
                  </div>
                  <div class="budget-item">
                    <div class="budget-label">餐饮费用</div>
                    <div class="budget-value">¥{{ tripPlan.budget.total_meals }}</div>
                  </div>
                  <div class="budget-item">
                    <div class="budget-label">交通费用</div>
                    <div class="budget-value">¥{{ tripPlan.budget.total_transportation }}</div>
                  </div>
                </div>
                <div class="budget-total">
                  <span class="total-label">预估总费用</span>
                  <span class="total-value">¥{{ tripPlan.budget.total }}</span>
                </div>
              </a-card>
            </div>

            <div class="right-map">
              <a-card id="map" title="📍 景点地图" :bordered="false" class="map-card">
                <div id="amap-container" style="width: 100%; height: 100%"></div>
              </a-card>
            </div>
          </div>

          <a-card title="📅 每日行程" :bordered="false" class="days-card">
            <a-collapse v-model:activeKey="activeDays" accordion>
              <a-collapse-panel
                v-for="(day, index) in tripPlan.days"
                :key="index"
                :id="`day-${index}`"
              >
                <template #header>
                  <div class="day-header">
                    <span class="day-title">第{{ day.day_index + 1 }}天</span>
                    <span class="day-date">{{ day.date }}</span>
                  </div>
                </template>

                <div class="day-info">
                  <div class="info-row">
                    <span class="label">📝 行程描述:</span>
                    <span class="value">{{ day.description }}</span>
                  </div>
                  <div class="info-row">
                    <span class="label">🚗 交通方式:</span>
                    <span class="value">{{ day.transportation }}</span>
                  </div>
                  <div class="info-row">
                    <span class="label">🏨 住宿:</span>
                    <span class="value">{{ day.accommodation }}</span>
                  </div>
                </div>

                <a-divider orientation="left">🎯 景点安排</a-divider>
                <a-list
                  :data-source="day.attractions"
                  :grid="{ gutter: 16, column: 2 }"
                >
                  <template #renderItem="{ item, index }">
                    <a-list-item>
                      <a-card :title="item.name" size="small" class="attraction-card">
                        <template #extra v-if="editMode">
                          <a-space>
                            <a-button
                              size="small"
                              @click="moveAttraction(day.day_index, index, 'up')"
                              :disabled="index === 0"
                            >
                              ↑
                            </a-button>
                            <a-button
                              size="small"
                              @click="moveAttraction(day.day_index, index, 'down')"
                              :disabled="index === day.attractions.length - 1"
                            >
                              ↓
                            </a-button>
                            <a-button
                              size="small"
                              danger
                              @click="deleteAttraction(day.day_index, index)"
                            >
                              🗑️
                            </a-button>
                          </a-space>
                        </template>

                        <div class="attraction-image-wrapper">
                          <img
                            :src="getAttractionImage(item.name, index)"
                            :alt="item.name"
                            class="attraction-image"
                            @error="handleImageError"
                          />
                          <div class="attraction-badge">
                            <span class="badge-number">{{ index + 1 }}</span>
                          </div>
                          <div v-if="item.ticket_price" class="price-tag">
                            ¥{{ item.ticket_price }}
                          </div>
                        </div>

                        <div v-if="editMode">
                          <p><strong>地址:</strong></p>
                          <a-input v-model:value="item.address" size="small" style="margin-bottom: 8px" />

                          <p><strong>游览时长(分钟):</strong></p>
                          <a-input-number v-model:value="item.visit_duration" :min="10" :max="480" size="small" style="width: 100%; margin-bottom: 8px" />

                          <p><strong>描述:</strong></p>
                          <a-textarea v-model:value="item.description" :rows="2" size="small" style="margin-bottom: 8px" />
                        </div>

                        <div v-else>
                          <p><strong>地址:</strong> {{ item.address }}</p>
                          <p><strong>游览时长:</strong> {{ item.visit_duration }}分钟</p>
                          <p><strong>描述:</strong> {{ item.description }}</p>
                          <p v-if="item.rating"><strong>评分:</strong> {{ item.rating }}⭐</p>
                        </div>
                      </a-card>
                    </a-list-item>
                  </template>
                </a-list>

                <a-divider v-if="day.hotel" orientation="left">🏨 住宿推荐</a-divider>
                <a-card v-if="day.hotel" size="small" class="hotel-card">
                  <template #title>
                    <span class="hotel-title">{{ day.hotel.name }}</span>
                  </template>
                  <a-descriptions :column="2" size="small">
                    <a-descriptions-item label="地址">{{ day.hotel.address }}</a-descriptions-item>
                    <a-descriptions-item label="类型">{{ day.hotel.type }}</a-descriptions-item>
                    <a-descriptions-item label="价格范围">{{ day.hotel.price_range }}</a-descriptions-item>
                    <a-descriptions-item label="评分">{{ day.hotel.rating }}⭐</a-descriptions-item>
                    <a-descriptions-item label="距离" :span="2">{{ day.hotel.distance }}</a-descriptions-item>
                  </a-descriptions>
                </a-card>

                <a-divider orientation="left">🍽️ 餐饮安排</a-divider>
                <a-descriptions :column="1" bordered size="small">
                  <a-descriptions-item
                    v-for="meal in day.meals"
                    :key="meal.type"
                    :label="getMealLabel(meal.type)"
                  >
                    {{ meal.name }}
                    <span v-if="meal.description"> - {{ meal.description }}</span>
                  </a-descriptions-item>
                </a-descriptions>
              </a-collapse-panel>
            </a-collapse>
          </a-card>

          <a-card id="weather" v-if="tripPlan.weather_info && tripPlan.weather_info.length > 0" title="天气信息" style="margin-top: 20px" :bordered="false">
            <a-list
              :data-source="tripPlan.weather_info"
              :grid="{ gutter: 16, column: 3 }"
            >
              <template #renderItem="{ item }">
                <a-list-item>
                  <a-card size="small" class="weather-card">
                    <div class="weather-date">{{ item.date }}</div>
                    <div class="weather-info-row">
                      <span class="weather-icon">☀️</span>
                      <div>
                        <div class="weather-label">白天</div>
                        <div class="weather-value">{{ item.day_weather }} {{ item.day_temp }}°C</div>
                      </div>
                    </div>
                    <div class="weather-info-row">
                      <span class="weather-icon">🌙</span>
                      <div>
                        <div class="weather-label">夜间</div>
                        <div class="weather-value">{{ item.night_weather }} {{ item.night_temp }}°C</div>
                      </div>
                    </div>
                    <div class="weather-wind">
                      💨 {{ item.wind_direction }} {{ item.wind_power }}
                    </div>
                  </a-card>
                </a-list-item>
              </template>
            </a-list>
          </a-card>

          <a-card id="feedback" title="✨ 行程评价" style="margin-top: 20px" :bordered="false" class="feedback-card">
            <TripFeedback
              :city="tripPlan.city"
              :userId="currentUserId"
            />
          </a-card>
        </div>
      </div>

      <a-empty v-else description="没有找到旅行计划数据">
        <template #image>
          <div style="font-size: 80px;">🗺️</div>
        </template>
        <template #description>
          <span style="color: #999;">暂无旅行计划数据,请先创建行程</span>
        </template>
        <a-button type="primary" @click="goBack">返回首页创建行程</a-button>
      </a-empty>
    </a-spin>

    <div class="refine-chat-bar" v-if="tripPlan && !editMode">
      <a-input-group compact class="chat-input-group">
        <a-input
          v-model:value="refineMessage"
          placeholder="对行程不满意？告诉 AI 您的想法 (例如：太贵了，换成穷游版；第二天太累了...)"
          @pressEnter="handleRefine"
          :disabled="isRefining"
          style="width: calc(100% - 120px);"
        />
        <a-button type="primary" @click="handleRefine" :loading="isRefining" style="width: 120px;">
          🪄 重新规划
        </a-button>
      </a-input-group>
    </div>

    <a-back-top :visibility-height="300">
      <div class="back-top-button">
        ↑
      </div>
    </a-back-top>
  </div>
</template>

<script setup lang="ts">
import TripFeedback from '@/views/TripFeedback.vue';
import { ref, onMounted, nextTick } from 'vue'
import { useRouter } from 'vue-router'
import { message } from 'ant-design-vue'
import { DownOutlined } from '@ant-design/icons-vue'
import AMapLoader from '@amap/amap-jsapi-loader'
import html2canvas from 'html2canvas'
import jsPDF from 'jspdf'
// 导入新增的方法和类型
import type { TripPlan, CriticScore } from '@/types'
import { refineTrip } from '@/services/api'

const currentUserId = ref('guest_user_123');
const router = useRouter()
const tripPlan = ref<TripPlan | null>(null)
const editMode = ref(false)
const originalPlan = ref<TripPlan | null>(null)
const attractionPhotos = ref<Record<string, string>>({})
const activeSection = ref('overview')
const activeDays = ref<number[]>([0])
let map: any = null

// --- 【新增的响应式状态】 ---
const criticScores = ref<CriticScore | null>(null)
const consumptionTier = ref<string>('')
const sessionId = ref<string>('')
const refineMessage = ref('')
const isRefining = ref(false)

onMounted(async () => {
  // 注意：我们这里改为读取完整的 'tripResponse' 对象
  const data = sessionStorage.getItem('tripResponse')
  if (data) {
    const res = JSON.parse(data)
    tripPlan.value = res.data
    criticScores.value = res.critic_scores
    consumptionTier.value = res.consumption_tier
    sessionId.value = res.session_id

    await loadAttractionPhotos()
    await nextTick()
    initMap()
  }
})

// --- 【新增：处理精修逻辑】 ---
const handleRefine = async () => {
  if (!refineMessage.value.trim()) {
    message.warning('请输入您的修改意见')
    return
  }
  if (!sessionId.value) {
    message.error('会话已失效，请返回首页重新生成')
    return
  }

  isRefining.value = true
  try {
    const res = await refineTrip(sessionId.value, refineMessage.value)
    if (res.success && res.data) {
      // 更新状态和界面数据
      tripPlan.value = res.data
      criticScores.value = res.critic_scores
      consumptionTier.value = res.consumption_tier || consumptionTier.value

      // 更新 sessionStorage
      sessionStorage.setItem('tripResponse', JSON.stringify(res))

      refineMessage.value = ''
      message.success('行程已根据您的要求重新推演！')

      // 重新绘制地图
      if (map) {
        map.destroy()
      }
      nextTick(() => {
        initMap()
      })
    }
  } catch (error: any) {
    console.error(error)
    message.error('行程调整失败：' + (error.message || '未知错误'))
  } finally {
    isRefining.value = false
  }
}

const goBack = () => {
  router.push('/')
}

const scrollToSection = ({ key }: { key: string }) => {
  activeSection.value = key
  const element = document.getElementById(key)
  if (element) {
    element.scrollIntoView({ behavior: 'smooth', block: 'start' })
  }
}

const toggleEditMode = () => {
  editMode.value = true
  originalPlan.value = JSON.parse(JSON.stringify(tripPlan.value))
  message.info('进入编辑模式')
}

const saveChanges = () => {
  editMode.value = false
  if (tripPlan.value) {
    // 兼容原有的保存逻辑，如果是手动编辑只覆盖 data
    const existing = JSON.parse(sessionStorage.getItem('tripResponse') || '{}')
    existing.data = tripPlan.value
    sessionStorage.setItem('tripResponse', JSON.stringify(existing))
  }
  message.success('修改已保存')
  if (map) {
    map.destroy()
  }
  nextTick(() => {
    initMap()
  })
}

const cancelEdit = () => {
  if (originalPlan.value) {
    tripPlan.value = JSON.parse(JSON.stringify(originalPlan.value))
  }
  editMode.value = false
  message.info('已取消编辑')
}

const deleteAttraction = (dayIndex: number, attrIndex: number) => {
  if (!tripPlan.value) return
  const day = tripPlan.value.days[dayIndex]
  if (day.attractions.length <= 1) {
    message.warning('每天至少需要保留一个景点')
    return
  }
  day.attractions.splice(attrIndex, 1)
  message.success('景点已删除')
}

const moveAttraction = (dayIndex: number, attrIndex: number, direction: 'up' | 'down') => {
  if (!tripPlan.value) return
  const day = tripPlan.value.days[dayIndex]
  const attractions = day.attractions
  if (direction === 'up' && attrIndex > 0) {
    [attractions[attrIndex], attractions[attrIndex - 1]] = [attractions[attrIndex - 1], attractions[attrIndex]]
  } else if (direction === 'down' && attrIndex < attractions.length - 1) {
    [attractions[attrIndex], attractions[attrIndex + 1]] = [attractions[attrIndex + 1], attractions[attrIndex]]
  }
}

const getMealLabel = (type: string): string => {
  const labels: Record<string, string> = {
    breakfast: '早餐',
    lunch: '午餐',
    dinner: '晚餐',
    snack: '小吃'
  }
  return labels[type] || type
}

const loadAttractionPhotos = async () => {
  if (!tripPlan.value) return
  const promises: Promise<void>[] = []
  tripPlan.value.days.forEach(day => {
    day.attractions.forEach(attraction => {
      const promise = fetch(`http://localhost:8000/api/poi/photo?name=${encodeURIComponent(attraction.name)}`)
        .then(res => res.json())
        .then(data => {
          if (data.success && data.data.photo_url) {
            attractionPhotos.value[attraction.name] = data.data.photo_url
          }
        })
        .catch(err => {
          console.error(`获取${attraction.name}图片失败:`, err)
        })
      promises.push(promise)
    })
  })
  await Promise.all(promises)
}

const getAttractionImage = (name: string, index: number): string => {
  if (attractionPhotos.value[name]) {
    return attractionPhotos.value[name]
  }
  const colors = [
    { start: '#667eea', end: '#764ba2' },
    { start: '#f093fb', end: '#f5576c' },
    { start: '#4facfe', end: '#00f2fe' },
    { start: '#43e97b', end: '#38f9d7' },
    { start: '#fa709a', end: '#fee140' }
  ]
  const colorIndex = index % colors.length
  const { start, end } = colors[colorIndex]
  const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="400" height="300">
    <defs>
      <linearGradient id="grad${index}" x1="0%" y1="0%" x2="100%" y2="100%">
        <stop offset="0%" style="stop-color:${start};stop-opacity:1" />
        <stop offset="100%" style="stop-color:${end};stop-opacity:1" />
      </linearGradient>
    </defs>
    <rect width="400" height="300" fill="url(#grad${index})"/>
    <text x="50%" y="50%" dominant-baseline="middle" text-anchor="middle" font-family="sans-serif" font-size="24" font-weight="bold" fill="white">${name}</text>
  </svg>`
  return `data:image/svg+xml;base64,${btoa(unescape(encodeURIComponent(svg)))}`
}

const handleImageError = (event: Event) => {
  const img = event.target as HTMLImageElement
  img.src = 'data:image/svg+xml,%3Csvg xmlns="http://www.w3.org/2000/svg" width="400" height="300"%3E%3Crect width="400" height="300" fill="%23f0f0f0"/%3E%3Ctext x="50%25" y="50%25" dominant-baseline="middle" text-anchor="middle" font-family="sans-serif" font-size="18" fill="%23999"%3E图片加载失败%3C/text%3E%3C/svg%3E'
}

const exportAsImage = async () => {
  // (保持原逻辑不变)
  try {
    message.loading({ content: '正在生成图片...', key: 'export', duration: 0 })
    const element = document.querySelector('.main-content') as HTMLElement
    if (!element) throw new Error('未找到内容元素')

    const exportContainer = document.createElement('div')
    exportContainer.style.width = element.offsetWidth + 'px'
    exportContainer.style.backgroundColor = '#f5f7fa'
    exportContainer.style.padding = '20px'
    exportContainer.innerHTML = element.innerHTML

    const mapContainer = document.getElementById('amap-container')
    if (mapContainer && map) {
      const mapCanvas = mapContainer.querySelector('canvas')
      if (mapCanvas) {
        const mapSnapshot = mapCanvas.toDataURL('image/png')
        const exportMapContainer = exportContainer.querySelector('#amap-container')
        if (exportMapContainer) {
          exportMapContainer.innerHTML = `<img src="${mapSnapshot}" style="width:100%;height:100%;object-fit:cover;" />`
        }
      }
    }

    const cards = exportContainer.querySelectorAll('.ant-card')
    cards.forEach((card) => {
      const cardEl = card as HTMLElement
      try {
        cardEl.className = ''
        cardEl.style.setProperty('background-color', '#ffffff')
        cardEl.style.setProperty('border-radius', '12px')
        cardEl.style.setProperty('box-shadow', '0 4px 12px rgba(0, 0, 0, 0.1)')
        cardEl.style.setProperty('margin-bottom', '20px')
        cardEl.style.setProperty('overflow', 'hidden')
      } catch (err) {}
    })

    const cardHeads = exportContainer.querySelectorAll('.ant-card-head')
    cardHeads.forEach((head) => {
      const headEl = head as HTMLElement
      try {
        headEl.style.setProperty('background-color', '#667eea')
        headEl.style.setProperty('color', '#ffffff')
        headEl.style.setProperty('padding', '16px 24px')
        headEl.style.setProperty('font-size', '18px')
        headEl.style.setProperty('font-weight', '600')
      } catch (err) {}
    })

    const cardBodies = exportContainer.querySelectorAll('.ant-card-body')
    cardBodies.forEach((body) => {
      const bodyEl = body as HTMLElement
      bodyEl.style.setProperty('background-color', '#ffffff')
      bodyEl.style.setProperty('padding', '24px')
    })

    const hotelCards = exportContainer.querySelectorAll('.hotel-card')
    hotelCards.forEach((card) => {
      const head = card.querySelector('.ant-card-head') as HTMLElement
      if (head) head.style.setProperty('background-color', '#1976d2')
      ;(card as HTMLElement).style.setProperty('background-color', '#e3f2fd')
    })

    const weatherCards = exportContainer.querySelectorAll('.weather-card')
    weatherCards.forEach((card) => {
      ;(card as HTMLElement).style.setProperty('background-color', '#e0f7fa')
    })

    const budgetTotal = exportContainer.querySelector('.budget-total')
    if (budgetTotal) {
      const el = budgetTotal as HTMLElement
      el.style.setProperty('background-color', '#667eea')
      el.style.setProperty('color', '#ffffff')
      el.style.setProperty('padding', '20px')
      el.style.setProperty('border-radius', '12px')
      el.style.setProperty('margin-bottom', '20px')
    }

    const budgetItems = exportContainer.querySelectorAll('.budget-item')
    budgetItems.forEach((item) => {
      const el = item as HTMLElement
      el.style.setProperty('background-color', '#f5f7fa')
      el.style.setProperty('padding', '16px')
      el.style.setProperty('border-radius', '8px')
      el.style.setProperty('margin-bottom', '12px')
    })

    exportContainer.style.position = 'absolute'
    exportContainer.style.left = '-9999px'
    document.body.appendChild(exportContainer)

    const canvas = await html2canvas(exportContainer, {
      backgroundColor: '#f5f7fa',
      scale: 2,
      logging: false,
      useCORS: true,
      allowTaint: true
    })
    document.body.removeChild(exportContainer)

    const link = document.createElement('a')
    link.download = `旅行计划_${tripPlan.value?.city}_${new Date().getTime()}.png`
    link.href = canvas.toDataURL('image/png')
    link.click()

    message.success({ content: '图片导出成功!', key: 'export' })
  } catch (error: any) {
    console.error('导出图片失败:', error)
    message.error({ content: `导出图片失败: ${error.message}`, key: 'export' })
  }
}

const exportAsPDF = async () => {
  // (保持原逻辑不变)
  try {
    message.loading({ content: '正在生成PDF...', key: 'export', duration: 0 })
    const element = document.querySelector('.main-content') as HTMLElement
    if (!element) throw new Error('未找到内容元素')

    const exportContainer = document.createElement('div')
    exportContainer.style.width = element.offsetWidth + 'px'
    exportContainer.style.backgroundColor = '#f5f7fa'
    exportContainer.style.padding = '20px'
    exportContainer.innerHTML = element.innerHTML

    const mapContainer = document.getElementById('amap-container')
    if (mapContainer && map) {
      const mapCanvas = mapContainer.querySelector('canvas')
      if (mapCanvas) {
        const mapSnapshot = mapCanvas.toDataURL('image/png')
        const exportMapContainer = exportContainer.querySelector('#amap-container')
        if (exportMapContainer) {
          exportMapContainer.innerHTML = `<img src="${mapSnapshot}" style="width:100%;height:100%;object-fit:cover;" />`
        }
      }
    }

    const cards = exportContainer.querySelectorAll('.ant-card')
    cards.forEach((card) => {
      const cardEl = card as HTMLElement
      try {
        cardEl.className = ''
        cardEl.style.setProperty('background-color', '#ffffff')
        cardEl.style.setProperty('border-radius', '12px')
        cardEl.style.setProperty('box-shadow', '0 4px 12px rgba(0, 0, 0, 0.1)')
        cardEl.style.setProperty('margin-bottom', '20px')
        cardEl.style.setProperty('overflow', 'hidden')
      } catch (err) {}
    })

    const cardHeads = exportContainer.querySelectorAll('.ant-card-head')
    cardHeads.forEach((head) => {
      const headEl = head as HTMLElement
      try {
        headEl.style.setProperty('background-color', '#667eea')
        headEl.style.setProperty('color', '#ffffff')
        headEl.style.setProperty('padding', '16px 24px')
        headEl.style.setProperty('font-size', '18px')
        headEl.style.setProperty('font-weight', '600')
      } catch (err) {}
    })

    const cardBodies = exportContainer.querySelectorAll('.ant-card-body')
    cardBodies.forEach((body) => {
      const bodyEl = body as HTMLElement
      bodyEl.style.setProperty('background-color', '#ffffff')
      bodyEl.style.setProperty('padding', '24px')
    })

    const hotelCards = exportContainer.querySelectorAll('.hotel-card')
    hotelCards.forEach((card) => {
      const head = card.querySelector('.ant-card-head') as HTMLElement
      if (head) head.style.setProperty('background-color', '#1976d2')
      ;(card as HTMLElement).style.setProperty('background-color', '#e3f2fd')
    })

    const weatherCards = exportContainer.querySelectorAll('.weather-card')
    weatherCards.forEach((card) => {
      ;(card as HTMLElement).style.setProperty('background-color', '#e0f7fa')
    })

    const budgetTotal = exportContainer.querySelector('.budget-total')
    if (budgetTotal) {
      const el = budgetTotal as HTMLElement
      el.style.setProperty('background-color', '#667eea')
      el.style.setProperty('color', '#ffffff')
      el.style.setProperty('padding', '20px')
      el.style.setProperty('border-radius', '12px')
      el.style.setProperty('margin-bottom', '20px')
    }

    const budgetItems = exportContainer.querySelectorAll('.budget-item')
    budgetItems.forEach((item) => {
      const el = item as HTMLElement
      el.style.setProperty('background-color', '#f5f7fa')
      el.style.setProperty('padding', '16px')
      el.style.setProperty('border-radius', '8px')
      el.style.setProperty('margin-bottom', '12px')
    })

    exportContainer.style.position = 'absolute'
    exportContainer.style.left = '-9999px'
    document.body.appendChild(exportContainer)

    const canvas = await html2canvas(exportContainer, {
      backgroundColor: '#f5f7fa',
      scale: 2,
      logging: false,
      useCORS: true,
      allowTaint: true
    })
    document.body.removeChild(exportContainer)

    const imgData = canvas.toDataURL('image/png')
    const pdf = new jsPDF({ orientation: 'portrait', unit: 'mm', format: 'a4' })

    const imgWidth = 210
    const imgHeight = (canvas.height * imgWidth) / canvas.width

    let heightLeft = imgHeight
    let position = 0

    pdf.addImage(imgData, 'PNG', 0, position, imgWidth, imgHeight)
    heightLeft -= 297

    while (heightLeft > 0) {
      position = heightLeft - imgHeight
      pdf.addPage()
      pdf.addImage(imgData, 'PNG', 0, position, imgWidth, imgHeight)
      heightLeft -= 297
    }

    pdf.save(`旅行计划_${tripPlan.value?.city}_${new Date().getTime()}.pdf`)
    message.success({ content: 'PDF导出成功!', key: 'export' })
  } catch (error: any) {
    console.error('导出PDF失败:', error)
    message.error({ content: `导出PDF失败: ${error.message}`, key: 'export' })
  }
}

const initMap = async () => {
  try {
    const AMap = await AMapLoader.load({
      key: import.meta.env.VITE_AMAP_WEB_JS_KEY,
      version: '2.0',
      plugins: ['AMap.Marker', 'AMap.Polyline', 'AMap.InfoWindow']
    })
    map = new AMap.Map('amap-container', {
      zoom: 12,
      center: [116.397128, 39.916527],
      viewMode: '3D'
    })
    addAttractionMarkers(AMap)
  } catch (error) {
    console.error('地图加载失败:', error)
  }
}

const addAttractionMarkers = (AMap: any) => {
  if (!tripPlan.value) return
  const markers: any[] = []
  const allAttractions: any[] = []

  tripPlan.value.days.forEach((day, dayIndex) => {
    day.attractions.forEach((attraction, attrIndex) => {
      if (attraction.location && attraction.location.longitude && attraction.location.latitude) {
        allAttractions.push({ ...attraction, dayIndex, attrIndex })
      }
    })
  })

  allAttractions.forEach((attraction, index) => {
    const marker = new AMap.Marker({
      position: [attraction.location.longitude, attraction.location.latitude],
      title: attraction.name,
      label: {
        content: `<div style="background: #4CAF50; color: white; padding: 4px 8px; border-radius: 4px; font-size: 12px;">${index + 1}</div>`,
        offset: new AMap.Pixel(0, -30)
      }
    })

    const infoWindow = new AMap.InfoWindow({
      content: `
        <div style="padding: 10px;">
          <h4 style="margin: 0 0 8px 0;">${attraction.name}</h4>
          <p style="margin: 4px 0;"><strong>地址:</strong> ${attraction.address}</p>
          <p style="margin: 4px 0;"><strong>游览时长:</strong> ${attraction.visit_duration}分钟</p>
          <p style="margin: 4px 0; color: #1890ff;"><strong>第${attraction.dayIndex + 1}天 景点${attraction.attrIndex + 1}</strong></p>
        </div>
      `,
      offset: new AMap.Pixel(0, -30)
    })

    marker.on('click', () => {
      infoWindow.open(map, marker.getPosition())
    })
    markers.push(marker)
  })

  map.add(markers)
  if (allAttractions.length > 0) {
    map.setFitView(markers)
  }
  drawRoutes(AMap, allAttractions)
}

const drawRoutes = (AMap: any, attractions: any[]) => {
  if (attractions.length < 2) return
  const dayGroups: any = {}
  attractions.forEach(attr => {
    if (!dayGroups[attr.dayIndex]) dayGroups[attr.dayIndex] = []
    dayGroups[attr.dayIndex].push(attr)
  })

  Object.values(dayGroups).forEach((dayAttractions: any) => {
    if (dayAttractions.length < 2) return
    const path = dayAttractions.map((attr: any) => [attr.location.longitude, attr.location.latitude])
    const polyline = new AMap.Polyline({
      path: path,
      strokeColor: '#1890ff',
      strokeWeight: 4,
      strokeOpacity: 0.8,
      strokeStyle: 'solid',
      showDir: true
    })
    map.add(polyline)
  })
}
</script>

<style scoped>
.result-container {
  min-height: 100vh;
  background: linear-gradient(135deg, #f5f7fa 0%, #c3cfe2 100%);
  padding: 40px 20px 100px; /* 底部增加内边距，给悬浮聊天框留出空间 */
  position: relative;
}

.page-header {
  max-width: 1200px;
  margin: 0 auto 30px;
  display: flex;
  justify-content: space-between;
  align-items: center;
  animation: fadeInDown 0.6s ease-out;
}

.back-button {
  border-radius: 8px;
  font-weight: 500;
}

/* 内容布局 */
.content-wrapper {
  max-width: 1400px;
  margin: 0 auto;
  display: flex;
  gap: 24px;
}

.side-nav {
  width: 240px;
  flex-shrink: 0;
}

.side-nav :deep(.ant-menu) {
  border-radius: 12px;
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.08);
  background: white;
}

.main-content {
  flex: 1;
  min-width: 0;
}

/* 新增的质检报告卡片样式 */
.critic-card {
  background-color: #f0f5ff;
}
.score-label {
  font-size: 13px;
  color: #666;
  margin-bottom: 4px;
}
.score-text {
  margin-left: 8px;
  font-weight: bold;
  color: #1890ff;
  font-size: 16px;
}
.critique-text {
  margin-top: 16px;
  padding-top: 16px;
  border-top: 1px dashed #d9d9d9;
  color: #595959;
}
.tier-tag {
  margin-top: 12px;
}

/* 底部悬浮聊天框样式 */
.refine-chat-bar {
  position: fixed;
  bottom: 0;
  left: 0;
  width: 100%;
  background: white;
  padding: 16px 24px;
  box-shadow: 0 -4px 12px rgba(0, 0, 0, 0.05);
  z-index: 1000;
  display: flex;
  justify-content: center;
}

.chat-input-group {
  max-width: 800px;
  width: 100%;
}

/* 景点图片样式及其他 (保持原有样式不变) */
.attraction-image-wrapper { position: relative; margin-bottom: 12px; border-radius: 8px; overflow: hidden; }
.attraction-image { width: 100%; height: 200px; object-fit: cover; transition: transform 0.3s ease; }
.attraction-image-wrapper:hover .attraction-image { transform: scale(1.05); }
.attraction-badge { position: absolute; top: 12px; left: 12px; background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); color: white; width: 36px; height: 36px; border-radius: 50%; display: flex; align-items: center; justify-content: center; font-weight: bold; box-shadow: 0 2px 8px rgba(0, 0, 0, 0.2); }
.badge-number { font-size: 18px; }
.price-tag { position: absolute; top: 12px; right: 12px; background: rgba(255, 77, 79, 0.9); color: white; padding: 4px 12px; border-radius: 12px; font-weight: bold; font-size: 14px; box-shadow: 0 2px 8px rgba(0, 0, 0, 0.2); }
.weather-card { background: linear-gradient(135deg, #e0f7fa 0%, #b2ebf2 100%); border: none !important; transition: all 0.3s ease; }
.weather-card:hover { transform: translateY(-4px); box-shadow: 0 8px 16px rgba(0, 0, 0, 0.15); }
.weather-date { font-size: 16px; font-weight: bold; color: #00796b; margin-bottom: 12px; text-align: center; }
.weather-info-row { display: flex; align-items: center; gap: 12px; margin-bottom: 8px; }
.weather-icon { font-size: 24px; }
.weather-label { font-size: 12px; color: #666; }
.weather-value { font-size: 16px; font-weight: 600; color: #00796b; }
.weather-wind { margin-top: 8px; padding-top: 8px; border-top: 1px solid rgba(0, 121, 107, 0.2); text-align: center; color: #00796b; font-size: 14px; }
.back-top-button { width: 50px; height: 50px; background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); color: white; border-radius: 50%; display: flex; align-items: center; justify-content: center; font-size: 24px; font-weight: bold; box-shadow: 0 4px 12px rgba(0, 0, 0, 0.3); cursor: pointer; transition: all 0.3s ease; }
.back-top-button:hover { transform: scale(1.1); box-shadow: 0 6px 16px rgba(0, 0, 0, 0.4); }
.hotel-card { background: linear-gradient(135deg, #e3f2fd 0%, #bbdefb 100%); border: none !important; }
.hotel-card :deep(.ant-card-head) { background: linear-gradient(135deg, #1976d2 0%, #1565c0 100%); }
.hotel-title { color: white !important; font-weight: 600; }
.top-info-section { display: flex; gap: 20px; margin-bottom: 20px; }
.left-info { flex: 0 0 400px; display: flex; flex-direction: column; gap: 20px; }
.right-map { flex: 1; }
.overview-card { height: fit-content; }
.overview-content { display: flex; flex-direction: column; gap: 12px; }
.info-item { display: flex; flex-direction: column; gap: 4px; }
.info-label { font-size: 14px; font-weight: 600; color: #666; }
.info-value { font-size: 15px; color: #333; line-height: 1.6; }
.budget-card { height: fit-content; }
.budget-grid { display: grid; grid-template-columns: repeat(2, 1fr); gap: 16px; margin-bottom: 16px; }
.budget-item { text-align: center; padding: 12px; background: linear-gradient(135deg, #f5f7fa 0%, #ffffff 100%); border-radius: 8px; border: 1px solid #e8e8e8; }
.budget-label { font-size: 13px; color: #666; margin-bottom: 8px; }
.budget-value { font-size: 20px; font-weight: 700; color: #1890ff; }
.budget-total { display: flex; justify-content: space-between; align-items: center; padding: 16px; background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); border-radius: 8px; color: white; }
.total-label { font-size: 16px; font-weight: 600; }
.total-value { font-size: 28px; font-weight: 700; }
.map-card { height: 100%; min-height: 500px; }
.map-card :deep(.ant-card-body) { height: calc(100% - 57px); padding: 0; }
.days-card { margin-top: 20px; }
.day-header { display: flex; justify-content: space-between; align-items: center; width: 100%; }
.day-title { font-size: 18px; font-weight: 600; color: #333; }
.day-date { font-size: 14px; color: #999; }
.day-info { margin-bottom: 20px; padding: 16px; background: linear-gradient(135deg, #f5f7fa 0%, #ffffff 100%); border-radius: 8px; border: 1px solid #e8e8e8; }
.info-row { display: flex; gap: 12px; margin-bottom: 8px; }
.info-row:last-child { margin-bottom: 0; }
.info-row .label { font-weight: 600; color: #666; min-width: 100px; }
.info-row .value { color: #333; flex: 1; }

:deep(.ant-card) { border-radius: 12px; box-shadow: 0 4px 12px rgba(0, 0, 0, 0.08); margin-bottom: 20px; transition: all 0.3s ease; animation: fadeInUp 0.6s ease-out; }
:deep(.ant-card:hover) { box-shadow: 0 8px 24px rgba(0, 0, 0, 0.12); }
:deep(.ant-card-head) { background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); color: white !important; border-radius: 12px 12px 0 0; font-weight: 600; }
:deep(.ant-card-head-title) { color: white !important; font-size: 18px; }
:deep(.ant-card-head-title span) { color: white !important; }
:deep(.ant-collapse) { border: none; background: transparent; }
:deep(.ant-collapse-item) { margin-bottom: 16px; border: 1px solid #e8e8e8; border-radius: 12px; overflow: hidden; }
:deep(.ant-collapse-header) { background: linear-gradient(135deg, #f5f7fa 0%, #ffffff 100%); padding: 16px 20px !important; font-weight: 600; }
:deep(.ant-collapse-content) { border-top: 1px solid #e8e8e8; }
:deep(.ant-collapse-content-box) { padding: 20px; }

@keyframes fadeInDown {
  from { opacity: 0; transform: translateY(-20px); }
  to { opacity: 1; transform: translateY(0); }
}
@keyframes fadeInUp {
  from { opacity: 0; transform: translateY(20px); }
  to { opacity: 1; transform: translateY(0); }
}

@media (max-width: 768px) {
  .result-container { padding: 20px 10px; }
  .page-header { flex-direction: column; gap: 16px; }
}
</style>