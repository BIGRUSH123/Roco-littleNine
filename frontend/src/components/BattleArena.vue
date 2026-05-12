<script setup>
import { ref, computed } from 'vue'

const props = defineProps({
  state: { type: Object, required: true },
  skillMap: { type: Object, default: () => ({}) },
  typeChart: { type: Object, default: () => ({}) }
})

const emit = defineEmits(['action'])

const showSwitchMenu = ref(false)

const player = computed(() => props.state.player_a)
const opponent = computed(() => props.state.player_b)
const active = computed(() => player.value.team[player.value.active_index])
const activeOpp = computed(() => opponent.value.team[opponent.value.active_index])
const isCharging = computed(() => !!active.value.charging)
const hasJealousy = computed(() => active.value.trait === '嫉妒')
const skillCount = computed(() => active.value.skills?.length || 0)
const gridCols = computed(() => {
  if (skillCount.value <= 4) return 'grid-cols-2'
  if (skillCount.value <= 6) return 'grid-cols-3'
  if (skillCount.value <= 8) return 'grid-cols-4'
  return 'grid-cols-5'
})
const btnPad = computed(() => skillCount.value > 6 ? 'py-1.5 px-1.5' : 'py-2.5 px-3')

function canUseSkill(skillName) {
  if (!isCharging.value) {
    const s = props.skillMap[skillName]
    if (!s) return false
    const energyCostMod = active.value.energy_cost_mod || 0
    const effectiveCost = Math.max(0, s.energy_cost + energyCostMod)
    return active.value.energy >= effectiveCost
  }
  if (hasJealousy.value) return true
  return skillName === active.value.charging
}

function skillMeta(name) {
  return props.skillMap[name] || null
}

function skillBrief(name) {
  const s = props.skillMap[name]
  if (!s) return ''
  const parts = [`${s.element}`]
  if (s.power > 0) parts.push(`${s.power}威`)
  const energyCostMod = active.value.energy_cost_mod || 0
  const effectiveCost = Math.max(0, s.energy_cost + energyCostMod)
  const costDisplay = effectiveCost !== s.energy_cost
    ? `${s.energy_cost}→${effectiveCost}费`
    : `${s.energy_cost}费`
  parts.push(costDisplay)
  if (s.priority > 0) parts.push(`+${s.priority}`)
  return parts.join('·')
}

function energyInsufficient(name) {
  if (isCharging.value) return false
  const s = props.skillMap[name]
  if (!s) return true
  const energyCostMod = active.value.energy_cost_mod || 0
  const effectiveCost = Math.max(0, s.energy_cost + energyCostMod)
  return active.value.energy < effectiveCost
}

function skillDesc(name) {
  const s = props.skillMap[name]
  return s?.description || ''
}

function typeEffectiveness(skillName) {
  const s = props.skillMap[skillName]
  if (!s) return 1.0
  const atkElem = s.element
  if (!atkElem) return 1.0
  const oppElement = activeOpp.value?.element || ''
  // Take first element (matching backend's single-element defense lookup)
  const defElem = oppElement.split(',')[0].trim()
  if (!defElem) return 1.0
  return (props.typeChart[atkElem] || {})[defElem] ?? 1.0
}

function effectivenessClass(skillName) {
  const m = typeEffectiveness(skillName)
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

const handleAction = (type, payload = null) => {
  emit('action', { type, payload })
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
            <div class="w-full h-3.5 bg-[#3a1a1a] rounded-sm overflow-hidden border border-[#4a3a3a]">
              <div
                class="h-full transition-all duration-400"
                :class="hpColorClass(active.current_hp, active.max_hp)"
                :style="{ width: hpPct(active.current_hp, active.max_hp) + '%' }"
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
                v-for="skill in active.skills"
                :key="skill"
                @click="handleAction('skill', skill)"
                :disabled="active.is_fainted || !canUseSkill(skill)"
                class="group relative bg-[#252830] hover:bg-[#2e3640] disabled:opacity-40 border text-[#e0e0e0] rounded text-left transition-colors"
                :class="[
                  btnPad,
                  effectivenessClass(skill) || 'border-[#3a3d42]',
                  energyInsufficient(skill) ? 'border-[#f44336]/50' : 'hover:border-[#4a90d9]'
                ]"
              >
                <div class="text-sm font-medium leading-tight truncate">{{ skill }}</div>
                <div class="text-[10px] leading-tight mt-0.5"
                  :class="energyInsufficient(skill) ? 'text-[#f44336]' : 'text-[#6a6d75]'"
                >{{ skillBrief(skill) }}</div>
                <div v-if="energyInsufficient(skill)" class="mt-0.5 text-[9px] text-[#f44336] font-medium">能量不足</div>
                <!-- Tooltip -->
                <div class="absolute bottom-full left-1/2 -translate-x-1/2 mb-2 px-3 py-2 bg-[#111318] border border-[#4a4d55] text-[#cdd6e0] text-xs rounded shadow-lg whitespace-nowrap max-w-xs truncate opacity-0 group-hover:opacity-100 transition-opacity pointer-events-none z-50">
                  {{ skillDesc(skill) }}
                </div>
              </button>
            </div>
            <!-- Utility Buttons -->
            <div class="flex gap-1.5">
              <button
                @click="handleAction('gather')"
                :disabled="active.is_fainted || (isCharging && !hasJealousy)"
                class="flex-1 bg-[#252830] hover:bg-[#2e3640] disabled:opacity-40 border border-[#3a3d42] hover:border-[#4a90d9] text-[#9a9da5] text-xs font-medium py-2 rounded transition-colors"
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
            </div>
            <div class="text-[10px] text-[#6a6d75] mt-0.5">Lv.100</div>
          </div>

          <!-- HP Bar -->
          <div class="w-64 mb-3">
            <div class="flex justify-between text-[10px] text-[#8a8d95] mb-0.5">
              <span>HP</span>
              <span>{{ activeOpp.current_hp }} / {{ activeOpp.max_hp }}</span>
            </div>
            <div class="w-full h-3.5 bg-[#3a1a1a] rounded-sm overflow-hidden border border-[#4a3a3a]">
              <div
                class="h-full transition-all duration-400"
                :class="hpColorClass(activeOpp.current_hp, activeOpp.max_hp)"
                :style="{ width: hpPct(activeOpp.current_hp, activeOpp.max_hp) + '%' }"
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

        <!-- Opponent Team Mini-view -->
        <div class="border-t border-[#3a3d42] p-3 bg-[#1e2128]">
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
