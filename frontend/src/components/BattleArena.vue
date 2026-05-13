<script setup>
import { ref, computed } from 'vue'

const props = defineProps({
  state: { type: Object, required: true },
  skillMap: { type: Object, default: () => ({}) },
  typeChart: { type: Object, default: () => ({}) },
  debugMode: { type: Boolean, default: false },
})

const emit = defineEmits(['action', 'debug-action'])

const showSwitchMenu = ref(false)
const showSwitchMenuOpp = ref(false)
const debugActionA = ref(null)
const debugActionB = ref(null)

const player = computed(() => props.state.player_a)
const opponent = computed(() => props.state.player_b)
const active = computed(() => player.value.team[player.value.active_index])
const activeOpp = computed(() => opponent.value.team[opponent.value.active_index])
const isCharging = computed(() => !!active.value.charging)
const hasJealousy = computed(() => active.value.trait === '嫉妒')
const isChargingOpp = computed(() => !!activeOpp.value.charging)
const hasJealousyOpp = computed(() => activeOpp.value.trait === '嫉妒')
const skillCount = computed(() => active.value.skills?.length || 0)
const skillCountOpp = computed(() => activeOpp.value.skills?.length || 0)
const gridCols = computed(() => {
  if (skillCount.value <= 4) return 'grid-cols-2'
  if (skillCount.value <= 6) return 'grid-cols-3'
  if (skillCount.value <= 8) return 'grid-cols-4'
  return 'grid-cols-5'
})
const gridColsOpp = computed(() => {
  if (skillCountOpp.value <= 4) return 'grid-cols-2'
  if (skillCountOpp.value <= 6) return 'grid-cols-3'
  if (skillCountOpp.value <= 8) return 'grid-cols-4'
  return 'grid-cols-5'
})
const btnPad = computed(() => skillCount.value > 6 ? 'py-1.5 px-1.5' : 'py-2.5 px-3')
const btnPadOpp = computed(() => skillCountOpp.value > 6 ? 'py-1.5 px-1.5' : 'py-2.5 px-3')

const markModA = computed(() => props.state.mark_energy_mod_a || 0)
const markModB = computed(() => props.state.mark_energy_mod_b || 0)

function getSpriteSkill(sprite, name) {
  return (sprite?.skills || []).find(s => s.name === name)
}

const canUseSkill = (sprite, skillName) => {
  const charging = !!sprite.charging
  const jealousy = sprite.trait === '嫉妒'
  if (!charging) {
    const ss = getSpriteSkill(sprite, skillName)
    if (!ss) return false
    if (ss.cooldown > 0) return false
    return sprite.energy >= ss.effective_energy_cost
  }
  if (jealousy) return true
  return skillName === sprite.charging
}

function skillBrief(sprite, name) {
  const sm = props.skillMap[name]
  if (!sm) return ''
  const ss = getSpriteSkill(sprite, name)
  const parts = [`${sm.element}`]
  if (ss && ss.effective_power > 0) parts.push(`${ss.effective_power}威`)
  parts.push(`${ss ? ss.effective_energy_cost : sm.energy_cost}费`)
  if (sm.priority > 0) parts.push(`+${sm.priority}`)
  return parts.join('·')
}

function energyInsufficient(sprite, name) {
  if (!!sprite.charging) return false
  const ss = getSpriteSkill(sprite, name)
  if (!ss) return true
  return sprite.energy < ss.effective_energy_cost
}

function skillDesc(name) {
  const s = props.skillMap[name]
  return s?.description || ''
}

function typeEffectiveness(skillName, targetElem) {
  const s = props.skillMap[skillName]
  if (!s) return 1.0
  const atkElem = s.element
  if (!atkElem) return 1.0
  const defElem = (targetElem || '').split(',')[0].trim()
  if (!defElem) return 1.0
  return (props.typeChart[atkElem] || {})[defElem] ?? 1.0
}

function effectivenessClass(skillName, targetElem) {
  const m = typeEffectiveness(skillName, targetElem)
  if (m >= 2.0) return 'border-[#4caf50] bg-[#1a2a1a]'
  if (m <= 0.5 && m > 0) return 'border-[#f4a236] bg-[#2a2010]'
  if (m === 0) return 'border-[#f44336] bg-[#2a1a1a]'
  return ''
}

const hpPct = (current, max) => max === 0 ? 0 : Math.max(0, Math.min(100, (current / max) * 100))
const hpColorClass = (current, max) => {
  if (max === 0) return 'bg-[#4a4d55]'
  const pct = current / max
  if (pct > 0.5) return 'bg-[#4caf50]'
  if (pct > 0.25) return 'bg-[#ffc107]'
  return 'bg-[#f44336]'
}

const freezeStacks = (sprite) => {
  if (!sprite.effects) return 0
  const f = sprite.effects.find(e => e.name === '冻结')
  return f ? f.stacks : 0
}
const freezePct = (sprite) => {
  if (!sprite.max_hp) return 0
  const stacks = freezeStacks(sprite)
  return Math.min(100, stacks * 5)
}

const handleAction = (type, payload = null) => {
  if (props.debugMode) {
    selectDebugAction('A', type, payload)
    return
  }
  emit('action', { type, payload })
}

const selectDebugAction = (side, type, payload) => {
  if (side === 'A') {
    debugActionA.value = { type, payload }
  } else {
    debugActionB.value = { type, payload }
  }
  if (debugActionA.value && debugActionB.value) {
    execDebugActions()
  }
}

const execDebugActions = () => {
  const actionA = debugActionA.value
  const actionB = debugActionB.value
  debugActionA.value = null
  debugActionB.value = null
  showSwitchMenu.value = false
  showSwitchMenuOpp.value = false

  const toDict = (a) => {
    const d = { type: a.type }
    if (a.type === 'skill') d.skill_name = a.payload
    if (a.type === 'switch') d.switch_index = a.payload
    return d
  }

  emit('debug-action', { actionA: toDict(actionA), actionB: toDict(actionB) })
}

const debugActionLabel = (action) => {
  if (!action) return '待选择'
  if (action.type === 'skill') return `技能: ${action.payload}`
  if (action.type === 'switch') return `换宠[${action.payload}]`
  if (action.type === 'gather') return '聚能'
  return action.type
}
</script>

<template>
  <div class="flex flex-col" style="min-height: calc(100vh - 49px)">

    <!-- Top Bar -->
    <div class="bg-[#252830] border-b border-[#3a3d42] px-4 py-1.5 flex items-center gap-4 text-xs">
      <span class="font-bold text-[#e0e0e0]">回合 {{ state.turn }}</span>
      <span class="text-[#8a8d95]">|</span>
      <span class="text-[#8a8d95]">
        {{ state.weather ? `天气: ${state.weather} (${state.weather_turns}t)` : '无天气' }}
      </span>
      <span v-if="state.is_finished" class="ml-auto font-bold text-[#f44336]">
        已结束 &mdash; {{ state.winner }} 获胜
      </span>
      <span v-else class="ml-auto text-[#6a6d75]">进行中</span>
    </div>

    <!-- Battle Field -->
    <div class="flex-1 flex flex-col lg:flex-row">

      <!-- Player Side -->
      <div class="flex-1 flex flex-col border-b lg:border-b-0 lg:border-r border-[#3a3d42]">
        <!-- Active Sprite -->
        <div class="flex-1 flex flex-col items-center justify-center p-6">
          <div class="text-center mb-4">
            <div class="text-lg font-bold text-[#e0e0e0]">
              {{ active.name }}
              <span v-if="active.is_fainted" class="text-[#f44336] text-xs ml-1">(力竭)</span>
              <span v-if="isCharging" class="text-[#ffc107] text-xs ml-1">蓄力-{{ active.charging }}</span>
            </div>
            <div class="text-[10px] text-[#6a6d75] mt-0.5">Lv.100</div>
          </div>

          <!-- HP Bar -->
          <div class="w-64 mb-3">
            <div class="flex justify-between text-[10px] text-[#8a8d95] mb-0.5">
              <span>HP</span>
              <span>{{ active.current_hp }} / {{ active.max_hp }}</span>
            </div>
            <div class="w-full h-3.5 bg-[#3a1a1a] rounded-sm overflow-hidden border border-[#4a3a3a] relative">
              <div
                class="h-full transition-all duration-400"
                :class="hpColorClass(active.current_hp, active.max_hp)"
                :style="{ width: hpPct(active.current_hp, active.max_hp) + '%' }"
              ></div>
              <!-- Freeze overlay -->
              <div
                v-if="freezeStacks(active) > 0"
                class="absolute top-0 left-0 h-full bg-gradient-to-r from-[#7ec8e3]/80 via-[#a0d8ef]/60 to-[#c8e8f8]/30 transition-all duration-400 rounded-sm"
                :style="{ width: Math.min(freezePct(active), hpPct(active.current_hp, active.max_hp)) + '%' }"
              ></div>
            </div>
          </div>

          <!-- Energy -->
          <div class="flex items-center gap-1 mb-3">
            <span class="text-[10px] text-[#6a6d75] mr-1">能量</span>
            <div class="flex gap-0.5">
              <div
                v-for="i in 10"
                :key="i"
                class="w-2 h-3 rounded-sm"
                :class="i <= active.energy ? 'bg-[#4a90d9]' : 'bg-[#2a2d35]'"
              ></div>
            </div>
            <span class="text-[10px] text-[#6a6d75] ml-1">{{ active.energy }}/10</span>
          </div>

          <!-- Effects -->
          <div v-if="active.effects && active.effects.length > 0" class="flex flex-wrap gap-1 justify-center">
            <span
              v-for="(eff, effIdx) in active.effects"
              :key="effIdx"
              class="px-1.5 py-0.5 text-[10px] rounded border"
              :class="eff.category === 'abnormal'
                ? 'bg-[#3a1a1a] border-[#5a2a2a] text-[#f44336]'
                : 'bg-[#1a2a1a] border-[#2a3a2a] text-[#4caf50]'"
            >
              {{ eff.name }}<template v-if="eff.stacks > 1"> &times;{{ eff.stacks }}</template>
            </span>
          </div>
        </div>

        <!-- Marks Bar -->
        <div v-if="props.state.marks_a && props.state.marks_a.length > 0" class="px-4 pb-2 flex flex-wrap gap-1">
          <span
            v-for="(m, mi) in props.state.marks_a"
            :key="mi"
            class="px-1.5 py-0.5 text-[10px] rounded border"
            :class="m.type === 'positive'
              ? 'bg-[#1a2a1a] border-[#2a3a2a] text-[#4caf50]'
              : 'bg-[#2a1a2a] border-[#3a2a3a] text-[#ce93d8]'"
          >
            [{{ m.name }}<template v-if="m.stacks > 1"> &times;{{ m.stacks }}</template>]
          </span>
        </div>

        <!-- Action Panel -->
        <div v-if="!state.is_finished" class="border-t border-[#3a3d42] p-3 bg-[#1e2128]">
          <template v-if="!showSwitchMenu">
            <!-- Skill Buttons -->
            <div :class="['grid gap-1.5 mb-1.5', gridCols]">
              <button
                v-for="sk in active.skills"
                :key="sk.name"
                @click="handleAction('skill', sk.name)"
                :disabled="active.is_fainted || !canUseSkill(active, sk.name)"
                class="group relative bg-[#252830] hover:bg-[#2e3640] disabled:opacity-40 border text-[#e0e0e0] rounded text-left transition-colors"
                :class="[
                  btnPad,
                  debugMode && debugActionA?.payload === sk.name ? 'border-[#4a90d9] ring-1 ring-[#4a90d9]/50' : effectivenessClass(sk.name, activeOpp?.element) || 'border-[#3a3d42]',
                  energyInsufficient(active, sk.name) ? 'border-[#f44336]/50' : 'hover:border-[#4a90d9]'
                ]"
              >
                <div class="text-sm font-medium leading-tight truncate">{{ sk.name }}</div>
                <div class="text-[10px] leading-tight mt-0.5"
                  :class="energyInsufficient(active, sk.name) ? 'text-[#f44336]' : 'text-[#6a6d75]'"
                >{{ skillBrief(active, sk.name) }}</div>
                <div v-if="energyInsufficient(active, sk.name)" class="mt-0.5 text-[9px] text-[#f44336] font-medium">能量不足</div>
                <div v-if="getSpriteSkill(active, sk.name)?.cooldown > 0" class="mt-0.5 text-[9px] text-[#ff9800] font-medium">冷却中</div>
                <!-- Tooltip -->
                <div class="absolute bottom-full left-1/2 -translate-x-1/2 mb-2 px-3 py-2 bg-[#111318] border border-[#4a4d55] text-[#cdd6e0] text-xs rounded shadow-lg whitespace-nowrap max-w-xs truncate opacity-0 group-hover:opacity-100 transition-opacity pointer-events-none z-50">
                  {{ skillDesc(sk.name) }}
                </div>
              </button>
            </div>
            <!-- Utility Buttons -->
            <div class="flex gap-1.5">
              <button
                @click="handleAction('gather')"
                :disabled="active.is_fainted || (isCharging && !hasJealousy)"
                class="flex-1 bg-[#252830] hover:bg-[#2e3640] disabled:opacity-40 border border-[#3a3d42] hover:border-[#4a90d9] text-[#9a9da5] text-xs font-medium py-2 rounded transition-colors"
                :class="debugMode && debugActionA?.type === 'gather' ? '!border-[#4a90d9] ring-1 ring-[#4a90d9]/50' : ''"
              >
                聚能
              </button>
              <button
                @click="showSwitchMenu = true"
                class="flex-1 bg-[#252830] hover:bg-[#2e3640] border border-[#3a3d42] hover:border-[#4a90d9] text-[#9a9da5] text-xs font-medium py-2 rounded transition-colors"
              >
                换宠
              </button>
            </div>
            <!-- Debug Status -->
            <div v-if="debugMode" class="mt-2 flex items-center gap-2 text-[10px]">
              <span class="px-2 py-0.5 rounded" :class="debugActionA ? 'bg-[#1a2a3a] text-[#4a90d9] border border-[#4a90d9]/50' : 'bg-[#2a1a1a] text-[#f44336] border border-[#f44336]/50'">
                我方: {{ debugActionLabel(debugActionA) }}
              </span>
              <span class="text-[#6a6d75]">|</span>
              <span class="px-2 py-0.5 rounded" :class="debugActionB ? 'bg-[#2a2010] text-[#ff9800] border border-[#ff9800]/50' : 'bg-[#2a1a1a] text-[#f44336] border border-[#f44336]/50'">
                对方: {{ debugActionLabel(debugActionB) }}
              </span>
            </div>
          </template>

          <!-- Switch Menu -->
          <template v-else>
            <div class="text-xs text-[#8a8d95] mb-2 text-center">选择替换上场:</div>
            <div class="grid grid-cols-3 gap-1.5 mb-1.5">
              <button
                v-for="(sprite, i) in player.team"
                :key="i"
                @click="handleAction('switch', i); showSwitchMenu = false"
                :disabled="i === player.active_index || sprite.is_fainted"
                class="bg-[#252830] hover:bg-[#2e3640] disabled:opacity-40 border text-left px-2.5 py-2 rounded transition-colors"
                :class="i === player.active_index ? 'border-[#4a90d9]' : 'border-[#3a3d42] hover:border-[#5a5d65]'"
              >
                <div class="text-xs font-bold text-[#e0e0e0] truncate">{{ sprite.name }}</div>
                <div class="text-[10px]" :class="sprite.is_fainted ? 'text-[#f44336]' : 'text-[#4caf50]'">
                  {{ sprite.is_fainted ? '力竭' : `${sprite.current_hp} HP` }}
                </div>
              </button>
            </div>
            <button
              @click="showSwitchMenu = false"
              class="w-full bg-[#2a2d35] hover:bg-[#3a3d45] text-[#8a8d95] text-xs py-1.5 rounded transition-colors"
            >
              取消
            </button>
          </template>
        </div>
      </div>

      <!-- Opponent Side -->
      <div class="flex-1 flex flex-col">
        <!-- Active Sprite -->
        <div class="flex-1 flex flex-col items-center justify-center p-6">
          <div class="text-center mb-4">
            <div class="text-lg font-bold text-[#e0e0e0]">
              {{ activeOpp.name }}
              <span v-if="activeOpp.is_fainted" class="text-[#f44336] text-xs ml-1">(力竭)</span>
              <span v-if="isChargingOpp" class="text-[#ffc107] text-xs ml-1">蓄力-{{ activeOpp.charging }}</span>
            </div>
            <div class="text-[10px] text-[#6a6d75] mt-0.5">Lv.100</div>
          </div>

          <!-- HP Bar -->
          <div class="w-64 mb-3">
            <div class="flex justify-between text-[10px] text-[#8a8d95] mb-0.5">
              <span>HP</span>
              <span>{{ activeOpp.current_hp }} / {{ activeOpp.max_hp }}</span>
            </div>
            <div class="w-full h-3.5 bg-[#3a1a1a] rounded-sm overflow-hidden border border-[#4a3a3a] relative">
              <div
                class="h-full transition-all duration-400"
                :class="hpColorClass(activeOpp.current_hp, activeOpp.max_hp)"
                :style="{ width: hpPct(activeOpp.current_hp, activeOpp.max_hp) + '%' }"
              ></div>
              <!-- Freeze overlay -->
              <div
                v-if="freezeStacks(activeOpp) > 0"
                class="absolute top-0 left-0 h-full bg-gradient-to-r from-[#7ec8e3]/80 via-[#a0d8ef]/60 to-[#c8e8f8]/30 transition-all duration-400 rounded-sm"
                :style="{ width: Math.min(freezePct(activeOpp), hpPct(activeOpp.current_hp, activeOpp.max_hp)) + '%' }"
              ></div>
            </div>
          </div>

          <!-- Energy -->
          <div class="flex items-center gap-1 mb-3">
            <span class="text-[10px] text-[#6a6d75] mr-1">能量</span>
            <div class="flex gap-0.5">
              <div
                v-for="i in 10"
                :key="i"
                class="w-2 h-3 rounded-sm"
                :class="i <= activeOpp.energy ? 'bg-[#4a90d9]' : 'bg-[#2a2d35]'"
              ></div>
            </div>
            <span class="text-[10px] text-[#6a6d75] ml-1">{{ activeOpp.energy }}/10</span>
          </div>

          <!-- Effects -->
          <div v-if="activeOpp.effects && activeOpp.effects.length > 0" class="flex flex-wrap gap-1 justify-center">
            <span
              v-for="(eff, effIdx) in activeOpp.effects"
              :key="effIdx"
              class="px-1.5 py-0.5 text-[10px] rounded border"
              :class="eff.category === 'abnormal'
                ? 'bg-[#3a1a1a] border-[#5a2a2a] text-[#f44336]'
                : 'bg-[#1a2a1a] border-[#2a3a2a] text-[#4caf50]'"
            >
              {{ eff.name }}<template v-if="eff.stacks > 1"> &times;{{ eff.stacks }}</template>
            </span>
          </div>
        </div>

        <!-- Marks Bar -->
        <div v-if="props.state.marks_b && props.state.marks_b.length > 0" class="px-4 pb-2 flex flex-wrap gap-1">
          <span
            v-for="(m, mi) in props.state.marks_b"
            :key="mi"
            class="px-1.5 py-0.5 text-[10px] rounded border"
            :class="m.type === 'positive'
              ? 'bg-[#1a2a1a] border-[#2a3a2a] text-[#4caf50]'
              : 'bg-[#2a1a2a] border-[#3a2a3a] text-[#ce93d8]'"
          >
            [{{ m.name }}<template v-if="m.stacks > 1"> &times;{{ m.stacks }}</template>]
          </span>
        </div>

        <!-- Opponent: Debug Action Panel -->
        <div v-if="debugMode && !state.is_finished" class="border-t border-[#ff9800]/50 p-3 bg-[#1e2128]">
          <div class="text-[10px] text-[#ff9800] mb-1 font-bold">[调试] 对手操作</div>
          <template v-if="!showSwitchMenuOpp">
            <div :class="['grid gap-1.5 mb-1.5', gridColsOpp]">
              <button
                v-for="sk in activeOpp.skills"
                :key="sk.name"
                @click="selectDebugAction('B', 'skill', sk.name)"
                :disabled="activeOpp.is_fainted || !canUseSkill(activeOpp, sk.name)"
                class="group relative bg-[#252830] hover:bg-[#2e3640] disabled:opacity-40 border text-[#e0e0e0] rounded text-left transition-colors"
                :class="[
                  btnPadOpp,
                  debugActionB?.payload === sk.name ? 'border-[#ff9800] ring-1 ring-[#ff9800]/50' : effectivenessClass(sk.name, active?.element) || 'border-[#3a3d42]',
                  energyInsufficient(activeOpp, sk.name) ? 'border-[#f44336]/50' : 'hover:border-[#ff9800]'
                ]"
              >
                <div class="text-sm font-medium leading-tight truncate">{{ sk.name }}</div>
                <div class="text-[10px] leading-tight mt-0.5"
                  :class="energyInsufficient(activeOpp, sk.name) ? 'text-[#f44336]' : 'text-[#6a6d75]'"
                >{{ skillBrief(activeOpp, sk.name) }}</div>
                <div v-if="energyInsufficient(activeOpp, sk.name)" class="mt-0.5 text-[9px] text-[#f44336] font-medium">能量不足</div>
                <div v-if="getSpriteSkill(activeOpp, sk.name)?.cooldown > 0" class="mt-0.5 text-[9px] text-[#ff9800] font-medium">冷却中</div>
</button>
            </div>
            <div class="flex gap-1.5">
              <button
                @click="selectDebugAction('B', 'gather')"
                :disabled="activeOpp.is_fainted"
                class="flex-1 bg-[#252830] hover:bg-[#2e3640] disabled:opacity-40 border text-xs font-medium py-2 rounded transition-colors"
                :class="debugActionB?.type === 'gather' ? 'border-[#ff9800] text-[#ff9800]' : 'border-[#3a3d42] text-[#9a9da5]'"
              >
                聚能
              </button>
              <button
                @click="showSwitchMenuOpp = true"
                class="flex-1 bg-[#252830] hover:bg-[#2e3640] border border-[#3a3d42] hover:border-[#ff9800] text-[#9a9da5] text-xs font-medium py-2 rounded transition-colors"
              >
                换宠
              </button>
            </div>
          </template>
          <template v-else>
            <div class="text-xs text-[#8a8d95] mb-2 text-center">选择对手替换上场:</div>
            <div class="grid grid-cols-3 gap-1.5 mb-1.5">
              <button
                v-for="(sprite, i) in opponent.team"
                :key="i"
                @click="selectDebugAction('B', 'switch', i); showSwitchMenuOpp = false"
                :disabled="i === opponent.active_index || sprite.is_fainted"
                class="bg-[#252830] hover:bg-[#2e3640] disabled:opacity-40 border text-left px-2.5 py-2 rounded transition-colors"
                :class="i === opponent.active_index ? 'border-[#4a90d9]' : 'border-[#3a3d42] hover:border-[#5a5d65]'"
              >
                <div class="text-xs font-bold text-[#e0e0e0] truncate">{{ sprite.name }}</div>
                <div class="text-[10px]" :class="sprite.is_fainted ? 'text-[#f44336]' : 'text-[#4caf50]'">
                  {{ sprite.is_fainted ? '力竭' : `${sprite.current_hp} HP` }}
                </div>
              </button>
            </div>
            <button
              @click="showSwitchMenuOpp = false"
              class="w-full bg-[#2a2d35] hover:bg-[#3a3d45] text-[#8a8d95] text-xs py-1.5 rounded transition-colors"
            >
              取消
            </button>
          </template>
        </div>

        <!-- Opponent: Normal Mini-view -->
        <div v-else class="border-t border-[#3a3d42] p-3 bg-[#1e2128]">
          <div class="grid grid-cols-3 gap-1.5">
            <div
              v-for="(sprite, i) in opponent.team"
              :key="i"
              class="bg-[#252830] border rounded px-2 py-1.5 text-center"
              :class="i === opponent.active_index ? 'border-[#4a90d9]' : 'border-[#3a3d42]'"
            >
              <div class="text-[10px] font-bold text-[#cdd6e0] truncate">{{ sprite.name }}</div>
              <div class="text-[10px]" :class="sprite.is_fainted ? 'text-[#f44336]' : 'text-[#4caf50]'">
                {{ sprite.is_fainted ? '力竭' : Math.round((sprite.current_hp / sprite.max_hp) * 100) + '%' }}
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>
