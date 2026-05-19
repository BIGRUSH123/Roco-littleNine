<script setup>
import { ref, onMounted, computed, nextTick } from 'vue'
import { Dialog, DialogPanel, TransitionRoot, TransitionChild } from '@headlessui/vue'

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

const API_BASE = '/api'

// 元素名 → elements.css class 映射（elements.css 使用短名）
const ELEMENT_NAME_MAP = { '普通': '普' }
function elementClass(el) {
  return `element-${ELEMENT_NAME_MAP[el] || el}`
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
    <div class="card bg-white rounded-xl overflow-hidden">
      <div class="px-5 py-3 border-b border-[#D4C8B8] flex items-center gap-2">
        <span class="title text-lg font-bold text-[#3D2B1F]">队伍配置</span>
        <span v-if="!loading && !error" class="text-xs text-[#6B5E4F]">
          已选 {{ selectedTeam.filter(s => s !== null).length }}/6
        </span>
      </div>

      <!-- Loading -->
      <div v-if="loading" class="p-8 text-center text-sm text-[#6B5E4F]">
        加载精灵数据中...
      </div>

      <!-- Error -->
      <div v-else-if="error" class="p-8 text-center">
        <div class="text-sm text-[#D4534A] mb-1">加载精灵失败</div>
        <div class="text-xs text-[#6B5E4F] mb-4">{{ error }}</div>
        <div class="text-xs text-[#6B5E4F] mb-3">
          请确认后端已启动（端口 8000）：
          <code class="bg-[#F5F2EC] px-1.5 py-0.5 rounded text-[#3D2B1F]">python scripts/api/main.py</code>
        </div>
        <button
          @click="loadSprites"
          class="btn btn-primary px-5 py-2 text-white text-xs font-bold rounded-lg transition-colors"
        >
          重试
        </button>
      </div>

      <!-- Team Slots -->
      <div v-else class="p-5">
        <div class="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
          <div
            v-for="(slot, idx) in 6"
            :key="idx"
            class="card bg-[#FBF7F0] rounded-xl min-h-[140px] transition-colors duration-150"
            :class="selectedTeam[idx]
              ? leadSlot === idx ? 'border-[#C9A96E] shadow-[0_0_12px_rgba(201,169,110,0.2)]' : 'border-[#D4C8B8] hover:border-[#C9A96E]'
              : 'border-dashed border-[#D4C8B8] hover:border-[#C9A96E]'"
          >
            <!-- Filled -->
            <template v-if="selectedTeam[idx]">
              <div class="p-3">
                <div class="flex items-center justify-between mb-2">
                  <div class="flex items-center gap-1.5 min-w-0">
                    <span class="text-[10px] text-[#6B5E4F] font-mono flex-shrink-0">{{ selectedTeam[idx].number ? String(selectedTeam[idx].number).padStart(3, '0') : '???' }}</span>
                    <span
                      v-if="selectedTeam[idx].element"
                      class="text-[10px] px-1 rounded border border-[#D4C8B8] text-[#6B5E4F] flex-shrink-0"
                    >{{ selectedTeam[idx].element }}</span>
                    <span class="text-sm font-bold text-[#3D2B1F] truncate">
                      {{ selectedTeam[idx].name }}
                    </span>
                  </div>
                  <button
                    v-if="leadSlot !== idx"
                    @click="leadSlot = idx"
                    class="text-[#6B5E4F] hover:text-[#C9A96E] text-[10px] px-1.5 py-0.5 rounded border border-[#D4C8B8] hover:border-[#C9A96E] transition-colors flex-shrink-0 ml-1"
                    title="设为首发"
                  >首发</button>
                  <span
                    v-else
                    class="text-[#C9A96E] text-[10px] px-1.5 py-0.5 rounded border border-[#C9A96E] flex-shrink-0 ml-1"
                  >★首发</span>
                  <button
                    @click="clearSlot(idx)"
                    class="text-[#6B5E4F] hover:text-[#D4534A] text-xs transition-colors flex-shrink-0 ml-1"
                    title="移除"
                  >&times;</button>
                </div>
                <div class="flex flex-wrap gap-1">
                  <button
                    v-for="skill in selectedTeam[idx].skills"
                    :key="skill"
                    @click="toggleSkill(idx, skill)"
                    :class="[
                      'group relative px-2 py-0.5 text-xs rounded-lg transition-colors text-left',
                      teamSkills[idx].includes(skill)
                        ? 'btn btn-primary text-white'
                        : 'bg-[#F5F2EC] text-[#6B5E4F] hover:bg-[#EDE8DF]'
                    ]"
                  >
                    {{ skill }}
                    <span class="hidden group-hover:block absolute bottom-full left-0 mb-1 px-2 py-1 bg-white border border-[#D4C8B8] rounded-lg text-[10px] text-[#3D2B1F] whitespace-nowrap z-10 shadow-lg">
                      {{ skillDesc(skill) || skill }}
                    </span>
                  </button>
                </div>
                <!-- 血脉选择 -->
                <div v-if="teamBloodlineOptions[idx].length > 0" class="mt-2 flex items-center gap-1.5">
                  <span class="text-[10px] text-[#6B5E4F] flex-shrink-0">血脉:</span>
                  <select
                    v-model="teamBloodlines[idx]"
                    class="bloodline-select bg-[#F5F2EC] border border-[#D4C8B8] rounded-lg px-2 py-0.5 text-[11px] text-[#3D2B1F] outline-none focus:border-[#C9A96E] flex-1 min-w-0"
                  >
                    <option v-for="bl in teamBloodlineOptions[idx]" :key="bl" :value="bl">{{ bl }}</option>
                  </select>
                </div>
                <div class="text-[10px] text-[#6B5E4F] mt-2">
                  {{ teamSkills[idx].length }}/10 技能
                </div>
              </div>
            </template>
            <!-- Empty -->
            <template v-else>
              <button
                @click="openPicker(idx)"
                class="w-full h-full min-h-[140px] flex items-center justify-center text-sm text-[#6B5E4F] hover:text-[#C9A96E] transition-colors"
              >
                + 添加精灵
              </button>
            </template>
          </div>
        </div>

        <!-- 道具选择 -->
        <div class="mt-5 pt-4 border-t border-[#D4C8B8]">
          <div class="text-xs font-bold text-[#3D2B1F] mb-3">携带道具（可选）</div>
          <div class="flex flex-wrap gap-2">
            <button
              @click="selectedItem = ''"
              :class="[
                'px-4 py-2 text-xs rounded-lg border transition-colors',
                !selectedItem
                  ? 'border-[#5C8D6E] bg-[#5C8D6E]/10 text-[#5C8D6E]'
                  : 'border-[#D4C8B8] bg-[#F5F2EC] text-[#6B5E4F] hover:border-[#C9A96E]'
              ]"
            >无道具</button>
            <button
              v-for="item in availableItems"
              :key="item.name"
              @click="selectedItem = item.name"
              :disabled="item.name === '进化之力' && !evolutionEligible"
              :class="[
                'group relative px-4 py-2 text-xs rounded-lg border transition-colors',
                selectedItem === item.name
                  ? 'border-[#5C8D6E] bg-[#5C8D6E]/10 text-[#5C8D6E]'
                  : item.name === '进化之力' && !evolutionEligible
                    ? 'border-[#D4C8B8] bg-[#F5F2EC] text-[#A89A8A] cursor-not-allowed opacity-50'
                    : 'border-[#D4C8B8] bg-[#F5F2EC] text-[#6B5E4F] hover:border-[#C9A96E]'
              ]"
            >
              {{ item.name }}
              <!-- 进化之力不可用的提示 -->
              <span v-if="item.name === '进化之力' && !evolutionEligible" class="block text-[10px] text-[#E0A030] mt-0.5">
                队伍中无精灵可进化
              </span>
            </button>
          </div>
          <!-- 选中道具说明 -->
          <div v-if="selectedItem" class="mt-2 text-[10px] text-[#6B5E4F]">
            <template v-for="item in availableItems" :key="item.name">
              <template v-if="item.name === selectedItem">
                {{ item.description }} · {{ item.cooldown_description }}
                <span v-if="item.requirement" class="text-[#E0A030]"> · {{ item.requirement }}</span>
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
              'px-10 py-2.5 text-sm font-bold rounded-lg transition-colors',
              isReady
                ? 'btn btn-primary text-white cursor-pointer'
                : 'bg-[#EDE8DF] text-[#A89A8A] cursor-not-allowed'
            ]"
          >
            开始战斗
          </button>
        </div>
      </div>
    </div>

    <!-- Sprite Picker Modal (Headless UI Dialog) -->
    <TransitionRoot :show="activeSlot >= 0" as="template">
      <Dialog class="relative z-50" @close="closePicker">
        <!-- Backdrop -->
        <TransitionChild
          as="template"
          enter="duration-200 ease-out"
          enter-from="opacity-0"
          enter-to="opacity-100"
          leave="duration-150 ease-in"
          leave-from="opacity-100"
          leave-to="opacity-0"
        >
          <div class="fixed inset-0 bg-black/15" />
        </TransitionChild>

        <div class="fixed inset-0 flex items-start justify-center pt-20">
          <TransitionChild
            as="template"
            enter="duration-200 ease-out"
            enter-from="opacity-0 scale-95"
            enter-to="opacity-100 scale-100"
            leave="duration-150 ease-in"
            leave-from="opacity-100 scale-100"
            leave-to="opacity-0 scale-95"
          >
            <DialogPanel class="card bg-white rounded-xl shadow-lg w-full max-w-md mx-4 overflow-hidden">
              <div class="p-3 border-b border-[#D4C8B8] space-y-2">
                <input
                  ref="searchInput"
                  v-model="searchText"
                  type="text"
                  placeholder="搜索精灵..."
                  class="w-full bg-[#FBF7F0] border border-[#D4C8B8] rounded-lg px-3 py-2 text-sm text-[#3D2B1F] placeholder-[#A89A8A] outline-none focus:border-[#C9A96E] transition-colors"
                />
                <!-- 属性筛选标签 -->
                <div class="flex flex-wrap gap-1">
                  <button
                    @click="elementFilter = ''"
                    class="px-2 py-0.5 text-[11px] rounded-lg border transition-colors"
                    :class="!elementFilter
                      ? 'bg-[#5C8D6E] border-[#5C8D6E] text-white'
                      : 'bg-[#F5F2EC] border-[#D4C8B8] text-[#6B5E4F] hover:border-[#C9A96E]'"
                  >全部</button>
                  <button
                    v-for="el in availableElements"
                    :key="el"
                    @click="elementFilter = elementFilter === el ? '' : el"
                    class="px-2 py-0.5 text-[11px] rounded-lg border transition-colors"
                    :class="elementFilter === el
                      ? [elementClass(el), 'border-transparent']
                      : 'bg-[#F5F2EC] border-[#D4C8B8] text-[#6B5E4F] hover:border-[#C9A96E]'"
                  >{{ el }}</button>
                </div>
              </div>
              <div class="max-h-72 overflow-y-auto p-1 vintage-scrollbar">
                <div v-if="filteredSprites.length === 0" class="p-4 text-center text-sm text-[#6B5E4F]">
                  未找到精灵
                </div>
                <button
                  v-for="sprite in filteredSprites"
                  :key="sprite.name"
                  @click="selectSprite(sprite)"
                  class="w-full text-left px-3 py-2 text-sm text-[#3D2B1F] hover:bg-[#F5F2EC] rounded-lg transition-colors flex items-center gap-2"
                >
                  <span class="text-xs text-[#6B5E4F] font-mono flex-shrink-0">{{ sprite.number ? String(sprite.number).padStart(3, '0') : '???' }}</span>
                  <span
                    v-if="sprite.element"
                    class="text-[10px] px-1 rounded border border-[#D4C8B8] text-[#6B5E4F] flex-shrink-0"
                  >{{ sprite.element }}</span>
                  <span class="truncate">{{ sprite.name }}</span>
                  <span class="text-xs text-[#6B5E4F] ml-auto flex-shrink-0">{{ sprite.skills.length }}技</span>
                </button>
              </div>
            </DialogPanel>
          </TransitionChild>
        </div>
      </Dialog>
    </TransitionRoot>

  </div>
</template>

<style scoped>
/* Styled dropdown arrow for bloodline select */
.bloodline-select {
  appearance: none;
  background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='10' height='6'%3E%3Cpath d='M0 0l5 6 5-6z' fill='%236B5E4F'/%3E%3C/svg%3E");
  background-repeat: no-repeat;
  background-position: right 6px center;
  padding-right: 22px;
}

/* Vintage scrollbar for sprite picker */
.vintage-scrollbar::-webkit-scrollbar {
  width: 6px;
}
.vintage-scrollbar::-webkit-scrollbar-track {
  background: #F5F2EC;
  border-radius: 3px;
}
.vintage-scrollbar::-webkit-scrollbar-thumb {
  background: #D4C8B8;
  border-radius: 3px;
}
.vintage-scrollbar::-webkit-scrollbar-thumb:hover {
  background: #B8A898;
}
</style>
