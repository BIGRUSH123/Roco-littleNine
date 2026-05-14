<script setup>
import { ref, onMounted, computed, nextTick } from 'vue'

const props = defineProps({
  skillMap: { type: Object, default: () => ({}) }
})

const emit = defineEmits(['start-battle'])

const availableSprites = ref([])
const selectedTeam = ref([null, null, null, null, null, null])
const teamSkills = ref([[], [], [], [], [], []])
const teamBloodlines = ref(['', '', '', '', '', ''])   // 血脉选择（每槽位）
const teamBloodlineOptions = ref([[], [], [], [], [], []]) // 每个槽位的可选血脉列表
const loading = ref(true)
const error = ref('')

const activeSlot = ref(-1)
const leadSlot = ref(-1)  // 首发精灵在 6 个槽位中的索引
const searchText = ref('')
const elementFilter = ref('')
const searchInput = ref(null)

// 道具选择
const selectedItem = ref('')          // '' | '愿力' | '进化之力'
const availableItems = ref([])
const evolutionEligible = ref(false)  // 队伍中是否有精灵可进化

const API_BASE = 'http://localhost:8000/api'

// 属性颜色映射
const elementColors = {
  '火': 'bg-[#f44336] text-white',
  '水': 'bg-[#2196f3] text-white',
  '草': 'bg-[#4caf50] text-white',
  '光': 'bg-[#ffc107] text-[#1a1d23]',
  '暗': 'bg-[#6a1b9a] text-white',
  '龙': 'bg-[#ff6f00] text-white',
  '电': 'bg-[#ffeb3b] text-[#1a1d23]',
  '冰': 'bg-[#80deea] text-[#1a1d23]',
  '虫': 'bg-[#8bc34a] text-[#1a1d23]',
  '毒': 'bg-[#9c27b0] text-white',
  '土': 'bg-[#795548] text-white',
  '地': 'bg-[#a1887f] text-white',
  '石': 'bg-[#757575] text-white',
  '钢': 'bg-[#b0bec5] text-[#1a1d23]',
  '翼': 'bg-[#90caf9] text-[#1a1d23]',
  '幻': 'bg-[#e040fb] text-white',
  '妖': 'bg-[#f48fb1] text-[#1a1d23]',
  '武': 'bg-[#d84315] text-white',
  '普': 'bg-[#9e9e9e] text-white',
  '普通': 'bg-[#9e9e9e] text-white',
  '幽灵': 'bg-[#5e35b1] text-white',
  '鬼': 'bg-[#5e35b1] text-white',
}

async function loadSprites() {
  loading.value = true
  error.value = ''
  try {
    const res = await fetch(`${API_BASE}/sprites`)
    if (!res.ok) throw new Error(`服务器返回 ${res.status}`)
    const data = await res.json()
    if (!data.sprites || !Array.isArray(data.sprites)) {
      throw new Error('数据格式错误')
    }
    availableSprites.value = data.sprites

    const savedTeam = localStorage.getItem('roco_team')
    if (savedTeam) {
      try {
        const parsed = JSON.parse(savedTeam)
        if (Array.isArray(parsed)) {
          parsed.forEach((member, index) => {
            if (member && index < 6) {
              const sprite = availableSprites.value.find(s => s.name === member.name)
              if (sprite) {
                selectedTeam.value[index] = sprite
                teamSkills.value[index] = member.skills || []
                // 加载血脉
                ;(async () => {
                  const blData = await loadSpriteBloodlines(sprite.name)
                  teamBloodlineOptions.value[index] = blData.bloodlines
                  teamBloodlines.value[index] = member.bloodline || blData.default
                })()
              }
            }
          })
          // 恢复道具选择
          const savedItem = localStorage.getItem('roco_item')
          if (savedItem) selectedItem.value = savedItem
          // 检查进化资格
          setTimeout(async () => {
            evolutionEligible.value = await checkEvolutionEligibility()
          }, 500)
        }
        const savedLead = localStorage.getItem('roco_lead')
        if (savedLead !== null) {
          const li = parseInt(savedLead)
          if (li >= 0 && li < 6 && selectedTeam.value[li]) leadSlot.value = li
        }
      } catch (e) {
        console.error('解析已保存队伍失败', e)
      }
    } else {
      // 默认测试阵容
      _loadDefaultTeam()
    }
    // 确保有有效首发
    if (leadSlot.value < 0 || !selectedTeam.value[leadSlot.value]) {
      leadSlot.value = selectedTeam.value.findIndex(s => s !== null)
    }
  } catch (e) {
    console.error('加载精灵失败:', e)
    error.value = e.message || '无法连接后端'
    availableSprites.value = []
  } finally {
    loading.value = false
  }
}

function _loadDefaultTeam() {
  // 预填测试阵容（仅首次，无 localStorage 时）
  const defaults = [
    { name: '迪莫', skills: ['龙吟','加固','蓄水','求雨'] },
    { name: '灵狐', skills: ['吹火','三连破','等价交换','暗箱操作'] },
    { name: '毛头小蛛', skills: ['毒针','烈焰风暴','暴风雪','孢子'] },
    { name: '丢丢（雪山附近的样子）', skills: ['聚盐','重击','水炮','孢子爆散'] },
  ]
  defaults.forEach((d, i) => {
    const sprite = availableSprites.value.find(s => s.name === d.name)
    if (sprite) {
      selectedTeam.value[i] = sprite
      teamSkills.value[i] = d.skills
    }
  })
  leadSlot.value = 0
}

onMounted(() => {
  loadSprites()
  loadItems()
})

async function loadItems() {
  try {
    const res = await fetch(`${API_BASE}/items`)
    if (res.ok) {
      const data = await res.json()
      availableItems.value = data.items || []
    }
  } catch (e) {
    console.error('加载道具失败:', e)
  }
}

async function loadSpriteBloodlines(spriteName) {
  try {
    const res = await fetch(`${API_BASE}/sprites/${encodeURIComponent(spriteName)}/bloodlines`)
    if (res.ok) {
      const data = await res.json()
      return { bloodlines: data.available_bloodlines || [], default: data.default_bloodline || '' }
    }
  } catch (e) { /* ignore */ }
  return { bloodlines: [], default: '' }
}

async function checkEvolutionEligibility() {
  // 检查队伍中任意精灵是否可进化
  for (const sprite of selectedTeam.value) {
    if (!sprite) continue
    try {
      const res = await fetch(`${API_BASE}/sprites/${encodeURIComponent(sprite.name)}/evolution`)
      if (res.ok) {
        const data = await res.json()
        if (data.can_evolve) return true
      }
    } catch (e) { /* ignore */ }
  }
  return false
}

function skillDesc(name) {
  const s = props.skillMap[name]
  if (!s) return ''
  const type = { '物攻': '物理', '魔攻': '魔法', '防御': '防御', '辅助': '辅助' }[s.skill_type] || s.skill_type
  const parts = [`[${s.element}]`, type]
  if (s.power > 0) parts.push(`${s.power}威力`)
  parts.push(`${s.energy_cost}费`)
  if (s.priority > 0) parts.push(`先手+${s.priority}`)
  return parts.join(' ')
}

const openPicker = async (slotIndex) => {
  activeSlot.value = slotIndex
  searchText.value = ''
  elementFilter.value = ''
  await nextTick()
  searchInput.value?.focus()
}

const closePicker = () => {
  activeSlot.value = -1
  searchText.value = ''
  elementFilter.value = ''
}

// 收集所有可用属性（支持多属性拆分）
const availableElements = computed(() => {
  const set = new Set()
  for (const s of availableSprites.value) {
    if (s.element) {
      s.element.split(',').forEach(e => {
        const el = e.trim()
        if (el) set.add(el)
      })
    }
  }
  return [...set].sort()
})

const selectSprite = async (sprite) => {
  if (activeSlot.value < 0) return

  const idx = activeSlot.value
  const isDuplicate = selectedTeam.value.some((s, i) => i !== idx && s?.name === sprite.name)
  if (isDuplicate) {
    alert('队伍中不能有重复精灵！')
    closePicker()
    return
  }

  selectedTeam.value[idx] = sprite
  teamSkills.value[idx] = sprite.skills.slice(0, Math.min(10, sprite.skills.length))
  closePicker()

  // 加载血脉选项
  const blData = await loadSpriteBloodlines(sprite.name)
  teamBloodlineOptions.value[idx] = blData.bloodlines
  teamBloodlines.value[idx] = blData.default
  evolutionEligible.value = await checkEvolutionEligibility()
}

const clearSlot = async (idx) => {
  selectedTeam.value[idx] = null
  teamSkills.value[idx] = []
  teamBloodlines.value[idx] = ''
  teamBloodlineOptions.value[idx] = []
  if (leadSlot.value === idx) {
    leadSlot.value = selectedTeam.value.findIndex(s => s !== null)
  }
  evolutionEligible.value = await checkEvolutionEligibility()
}

const toggleSkill = (slotIndex, skillName) => {
  const current = teamSkills.value[slotIndex]
  const i = current.indexOf(skillName)
  if (i > -1) {
    current.splice(i, 1)
  } else if (current.length < 10) {
    current.push(skillName)
  }
}

function formatName(sprite) {
  const num = sprite.number ? String(sprite.number).padStart(3, '0') : '???'
  return `${num}_${sprite.name}`
}

const filteredSprites = computed(() => {
  let list = availableSprites.value

  // 属性筛选
  if (elementFilter.value) {
    list = list.filter(s =>
      s.element && s.element.split(',').map(e => e.trim()).includes(elementFilter.value)
    )
  }

  // 文字搜索
  if (searchText.value) {
    const q = searchText.value.toLowerCase()
    list = list.filter(s =>
      s.name.toLowerCase().includes(q) ||
      formatName(s).toLowerCase().includes(q) ||
      (s.element && s.element.includes(q)) ||
      (s.number && String(s.number).includes(q))
    )
  }

  return list
})

const isReady = computed(() => {
  for (let i = 0; i < 6; i++) {
    if (selectedTeam.value[i] && teamSkills.value[i].length > 0) return true
  }
  return false
})

const startBattle = () => {
  if (!isReady.value) return
  const team = []
  const slotToTeam = {}  // UI slot → team array index
  for (let i = 0; i < 6; i++) {
    if (selectedTeam.value[i]) {
      slotToTeam[i] = team.length
      team.push({
        name: selectedTeam.value[i].name,
        skills: teamSkills.value[i],
        bloodline: teamBloodlines.value[i] || undefined,
      })
    }
  }
  const leadIndex = slotToTeam[leadSlot.value] ?? 0
  localStorage.setItem('roco_team', JSON.stringify(team))
  localStorage.setItem('roco_lead', leadSlot.value)
  localStorage.setItem('roco_item', selectedItem.value)
  emit('start-battle', { team, leadIndex, item: selectedItem.value || undefined })
}
</script>

<template>
  <div class="p-4 md:p-6">

    <!-- Team Builder -->
    <div class="bg-[#252830] border border-[#3a3d42] rounded">
      <div class="px-4 py-2.5 border-b border-[#3a3d42] flex items-center gap-2">
        <span class="text-sm font-bold text-[#e0e0e0]">队伍配置</span>
        <span v-if="!loading && !error" class="text-xs text-[#6a6d75]">
          已选 {{ selectedTeam.filter(s => s !== null).length }}/6
        </span>
      </div>

      <!-- Loading -->
      <div v-if="loading" class="p-8 text-center text-sm text-[#6a6d75]">
        加载精灵数据中...
      </div>

      <!-- Error -->
      <div v-else-if="error" class="p-8 text-center">
        <div class="text-sm text-[#f44336] mb-1">加载精灵失败</div>
        <div class="text-xs text-[#6a6d75] mb-4">{{ error }}</div>
        <div class="text-xs text-[#5a5d65] mb-3">
          请确认后端已启动（端口 8000）：
          <code class="bg-[#1e2128] px-1.5 py-0.5 rounded text-[#9a9da5]">python scripts/api/main.py</code>
        </div>
        <button
          @click="loadSprites"
          class="px-4 py-1.5 bg-[#4a90d9] hover:bg-[#5a9fe9] text-white text-xs font-bold rounded transition-colors"
        >
          重试
        </button>
      </div>

      <!-- Team Slots -->
      <div v-else class="p-4">
        <div class="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
          <div
            v-for="(slot, idx) in 6"
            :key="idx"
            class="bg-[#1e2128] border rounded min-h-[140px]"
            :class="selectedTeam[idx]
              ? leadSlot === idx ? 'border-[#ffc107]' : 'border-[#3a3d42] hover:border-[#5a5d65]'
              : 'border-dashed border-[#3a3d42] hover:border-[#5a5d65]'"
          >
            <!-- Filled -->
            <template v-if="selectedTeam[idx]">
              <div class="p-3">
                <div class="flex items-center justify-between mb-2">
                  <div class="flex items-center gap-1.5 min-w-0">
                    <span class="text-[10px] text-[#6a6d75] font-mono flex-shrink-0">{{ selectedTeam[idx].number ? String(selectedTeam[idx].number).padStart(3, '0') : '???' }}</span>
                    <span
                      v-if="selectedTeam[idx].element"
                      class="text-[10px] px-1 rounded border border-[#3a3d42] text-[#8a8d95] flex-shrink-0"
                    >{{ selectedTeam[idx].element }}</span>
                    <span class="text-sm font-bold text-[#e0e0e0] truncate">
                      {{ selectedTeam[idx].name }}
                    </span>
                  </div>
                  <button
                    v-if="leadSlot !== idx"
                    @click="leadSlot = idx"
                    class="text-[#6a6d75] hover:text-[#ffc107] text-[10px] px-1 py-0.5 rounded border border-[#3a3d42] hover:border-[#ffc107] transition-colors flex-shrink-0 ml-1"
                    title="设为首发"
                  >首发</button>
                  <span
                    v-else
                    class="text-[#ffc107] text-[10px] px-1 py-0.5 rounded border border-[#ffc107] flex-shrink-0 ml-1"
                  >★首发</span>
                  <button
                    @click="clearSlot(idx)"
                    class="text-[#6a6d75] hover:text-[#f44336] text-xs transition-colors flex-shrink-0 ml-1"
                    title="移除"
                  >&times;</button>
                </div>
                <div class="flex flex-wrap gap-1">
                  <button
                    v-for="skill in selectedTeam[idx].skills"
                    :key="skill"
                    @click="toggleSkill(idx, skill)"
                    :class="[
                      'group relative px-2 py-0.5 text-xs rounded transition-colors text-left',
                      teamSkills[idx].includes(skill)
                        ? 'bg-[#4a90d9] text-white'
                        : 'bg-[#2a2d35] text-[#8a8d95] hover:bg-[#3a3d45]'
                    ]"
                  >
                    {{ skill }}
                    <span class="hidden group-hover:block absolute bottom-full left-0 mb-1 px-2 py-1 bg-[#1a1d23] border border-[#3a3d42] rounded text-[10px] text-[#9a9da5] whitespace-nowrap z-10 shadow-lg">
                      {{ skillDesc(skill) || skill }}
                    </span>
                  </button>
                </div>
                <!-- 血脉选择 -->
                <div v-if="teamBloodlineOptions[idx].length > 0" class="mt-2 flex items-center gap-1.5">
                  <span class="text-[10px] text-[#6a6d75] flex-shrink-0">血脉:</span>
                  <select
                    v-model="teamBloodlines[idx]"
                    class="bg-[#1e2128] border border-[#3a3d42] rounded px-1.5 py-0.5 text-[11px] text-[#cdd6e0] outline-none focus:border-[#4a90d9] flex-1 min-w-0"
                  >
                    <option v-for="bl in teamBloodlineOptions[idx]" :key="bl" :value="bl">{{ bl }}</option>
                  </select>
                </div>
                <div class="text-[10px] text-[#5a5d65] mt-2">
                  {{ teamSkills[idx].length }}/10 技能
                </div>
              </div>
            </template>
            <!-- Empty -->
            <template v-else>
              <button
                @click="openPicker(idx)"
                class="w-full h-full min-h-[140px] flex items-center justify-center text-sm text-[#6a6d75] hover:text-[#9a9da5] transition-colors"
              >
                + 添加精灵
              </button>
            </template>
          </div>
        </div>

        <!-- 道具选择 -->
        <div class="mt-5 pt-4 border-t border-[#3a3d42]">
          <div class="text-xs font-bold text-[#e0e0e0] mb-3">携带道具（可选）</div>
          <div class="flex flex-wrap gap-2">
            <button
              @click="selectedItem = ''"
              :class="[
                'px-4 py-2 text-xs rounded border transition-colors',
                !selectedItem
                  ? 'border-[#4a90d9] bg-[#1a2a3a] text-[#4a90d9]'
                  : 'border-[#3a3d42] bg-[#1e2128] text-[#8a8d95] hover:border-[#5a5d65]'
              ]"
            >无道具</button>
            <button
              v-for="item in availableItems"
              :key="item.name"
              @click="selectedItem = item.name"
              :disabled="item.name === '进化之力' && !evolutionEligible"
              :class="[
                'group relative px-4 py-2 text-xs rounded border transition-colors',
                selectedItem === item.name
                  ? 'border-[#4a90d9] bg-[#1a2a3a] text-[#4a90d9]'
                  : item.name === '进化之力' && !evolutionEligible
                    ? 'border-[#3a3d42] bg-[#1e2128] text-[#5a5d65] cursor-not-allowed opacity-40'
                    : 'border-[#3a3d42] bg-[#1e2128] text-[#8a8d95] hover:border-[#5a5d65]'
              ]"
            >
              {{ item.name }}
              <!-- 进化之力不可用的提示 -->
              <span v-if="item.name === '进化之力' && !evolutionEligible" class="block text-[10px] text-[#f4a236] mt-0.5">
                队伍中无精灵可进化
              </span>
            </button>
          </div>
          <!-- 选中道具说明 -->
          <div v-if="selectedItem" class="mt-2 text-[10px] text-[#6a6d75]">
            <template v-for="item in availableItems" :key="item.name">
              <template v-if="item.name === selectedItem">
                {{ item.description }} · {{ item.cooldown_description }}
                <span v-if="item.requirement" class="text-[#ff9800]"> · {{ item.requirement }}</span>
              </template>
            </template>
          </div>
        </div>

        <!-- Start -->
        <div class="mt-5 text-center">
          <button
            @click="startBattle"
            :disabled="!isReady"
            :class="[
              'px-10 py-2.5 text-sm font-bold rounded transition-colors',
              isReady
                ? 'bg-[#4a90d9] text-white hover:bg-[#5a9fe9] cursor-pointer'
                : 'bg-[#2a2d35] text-[#5a5d65] cursor-not-allowed'
            ]"
          >
            开始战斗
          </button>
        </div>
      </div>
    </div>

    <!-- Sprite Picker Modal -->
    <Transition name="modal">
      <div
        v-if="activeSlot >= 0"
        class="fixed inset-0 z-50 flex items-start justify-center pt-20"
        @click.self="closePicker"
      >
        <div class="bg-[#252830] border border-[#3a3d42] rounded shadow-lg w-full max-w-md mx-4" @click.stop>
          <div class="p-3 border-b border-[#3a3d42] space-y-2">
            <input
              ref="searchInput"
              v-model="searchText"
              type="text"
              placeholder="搜索精灵..."
              class="w-full bg-[#1e2128] border border-[#3a3d42] rounded px-3 py-1.5 text-sm text-[#e0e0e0] placeholder-[#5a5d65] outline-none focus:border-[#4a90d9]"
              @keydown.escape="closePicker"
            />
            <!-- 属性筛选标签 -->
            <div class="flex flex-wrap gap-1">
              <button
                @click="elementFilter = ''"
                class="px-1.5 py-0.5 text-[11px] rounded border transition-colors"
                :class="!elementFilter
                  ? 'bg-[#4a90d9] border-[#4a90d9] text-white'
                  : 'bg-[#1e2128] border-[#3a3d42] text-[#8a8d95] hover:border-[#5a5d65]'"
              >全部</button>
              <button
                v-for="el in availableElements"
                :key="el"
                @click="elementFilter = elementFilter === el ? '' : el"
                class="px-1.5 py-0.5 text-[11px] rounded border transition-colors"
                :class="elementFilter === el
                  ? (elementColors[el] || 'bg-[#4a90d9] text-white') + ' border-transparent'
                  : 'bg-[#1e2128] border-[#3a3d42] text-[#8a8d95] hover:border-[#5a5d65]'"
              >{{ el }}</button>
            </div>
          </div>
          <div class="max-h-72 overflow-y-auto p-1">
            <div v-if="filteredSprites.length === 0" class="p-4 text-center text-sm text-[#6a6d75]">
              未找到精灵
            </div>
            <button
              v-for="sprite in filteredSprites"
              :key="sprite.name"
              @click="selectSprite(sprite)"
              class="w-full text-left px-3 py-1.5 text-sm text-[#cdd6e0] hover:bg-[#2a2d35] rounded transition-colors flex items-center gap-2"
            >
              <span class="text-xs text-[#6a6d75] font-mono flex-shrink-0">{{ sprite.number ? String(sprite.number).padStart(3, '0') : '???' }}</span>
              <span
                v-if="sprite.element"
                class="text-[10px] px-1 rounded border border-[#3a3d42] text-[#8a8d95] flex-shrink-0"
              >{{ sprite.element }}</span>
              <span class="truncate">{{ sprite.name }}</span>
              <span class="text-xs text-[#5a5d65] ml-auto flex-shrink-0">{{ sprite.skills.length }}技</span>
            </button>
          </div>
        </div>
      </div>
    </Transition>

  </div>
</template>

<style scoped>
.modal-enter-active,
.modal-leave-active {
  transition: opacity 0.15s ease;
}
.modal-enter-active > div,
.modal-leave-active > div {
  transition: transform 0.15s ease;
}
.modal-enter-from,
.modal-leave-to {
  opacity: 0;
}
.modal-enter-from > div {
  transform: scale(0.95);
}
.modal-leave-to > div {
  transform: scale(0.95);
}
</style>
