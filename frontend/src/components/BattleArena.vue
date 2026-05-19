<script setup>
import { ref, computed } from 'vue'
import { useBattleStore } from '../stores/battle.js'
import { useSpriteAssetStore } from '../stores/spriteAssets.js'
import SpriteCard from './SpriteCard.vue'
import EffectCard from './EffectCard.vue'
import SkillButton from './SkillButton.vue'

const props = defineProps({
  skillMap: { type: Object, default: () => ({}) },
  typeChart: { type: Object, default: () => ({}) },
  debugMode: { type: Boolean, default: false },
})

const emit = defineEmits(['action', 'debug-action'])

const store = useBattleStore()
const _spriteAssets = useSpriteAssetStore()

const showSwitchMenu = ref(false)
const showSwitchMenuOpp = ref(false)
const debugActionA = ref(null)
const debugActionB = ref(null)

const player = computed(() => ({
  team: store.selfTeam,
  active_index: store.activeIndexA,
  item: store.selfItem,
}))
const opponent = computed(() => ({
  team: store.oppTeam,
  active_index: store.activeIndexB,
}))
const active = computed(() => store.selfSprite)
const activeOpp = computed(() => store.oppSprite)
const isCharging = computed(() => !!active.value?.charging)
const hasJealousy = computed(() => active.value?.trait === '嫉妒')
const isChargingOpp = computed(() => !!activeOpp.value?.charging)
const hasJealousyOpp = computed(() => activeOpp.value?.trait === '嫉妒')
const skillCount = computed(() => active.value?.skills?.length || 0)
const skillCountOpp = computed(() => activeOpp.value?.skills?.length || 0)
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

const markModA = computed(() => store.markEnergyModA || 0)
const markModB = computed(() => store.markEnergyModB || 0)

const canUseItem = computed(() => {
  const item = player.value?.item
  if (!item || item.is_exhausted) return false
  if (item.last_use_turn > 0 && store.turn - item.last_use_turn < item.cooldown_turns) return false
  return true
})

function getSpriteSkill(sprite, name) {
  return (sprite?.skills || []).find(s => s.name === name)
}

const canUseSkill = (sprite, skillName) => {
  const charging = !!sprite?.charging
  const jealousy = sprite?.trait === '嫉妒'
  if (!charging) {
    const ss = getSpriteSkill(sprite, skillName)
    if (!ss) return false
    if (ss.cooldown > 0) return false
    return (sprite?.energy ?? 0) >= ss.effective_energy_cost
  }
  if (jealousy) return true
  return skillName === sprite.charging
}

function skillBrief(sprite, name) {
  const sm = props.skillMap[name]
  if (!sm) return ''
  const ss = getSpriteSkill(sprite, name)
  const parts = [`${sm.element}`]
  if (ss && ss.effective_power > 0) {
    const p = ss.effective_power
    const bonus = ss.position_power_bonus || 0
    parts.push(bonus > 0 ? `${p}威(+${bonus})` : `${p}威`)
  }
  if (ss && ss.base_energy_cost > 0 && ss.effective_energy_cost < ss.base_energy_cost) {
    const saving = ss.base_energy_cost - ss.effective_energy_cost
    parts.push(`${ss.effective_energy_cost}费(-${saving})`)
  } else {
    parts.push(`${ss ? ss.effective_energy_cost : sm.energy_cost}费`)
  }
  if (sm.priority > 0) parts.push(`+${sm.priority}`)
  return parts.join('·')
}

function skillBadges(sprite, name) {
  const ss = getSpriteSkill(sprite, name)
  if (!ss) return []
  const badges = []
  const tx = ss.transmission || 0
  if (ss.main_axis) badges.push('主轴')
  else if (tx > 0) badges.push(`传${tx}`)
  return badges
}

function energyInsufficient(sprite, name) {
  if (!!sprite?.charging) return false
  const ss = getSpriteSkill(sprite, name)
  if (!ss) return true
  return (sprite?.energy ?? 0) < ss.effective_energy_cost
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
  if (m >= 2.0) return 'border-[#6DBF7C] bg-[#E8F5E9]'
  if (m <= 0.5 && m > 0) return 'border-[#EF6C00] bg-[#FFF3E0]'
  if (m === 0) return 'border-[#D4534A] bg-[#FFEBEE]'
  return ''
}

const hpPct = (current, max) => max === 0 ? 0 : Math.max(0, Math.min(100, (current / max) * 100))
const hpColorClass = (current, max) => {
  if (max === 0) return 'bg-[#D4C8B8]'
  const pct = current / max
  if (pct > 0.5) return 'bg-[#6DBF7C]'
  if (pct > 0.25) return 'bg-[#C9A96E]'
  return 'bg-[#D4534A]'
}

const freezeStacks = (sprite) => {
  if (!sprite?.effects) return 0
  const f = sprite.effects.find(e => e.name === '冻结')
  return f ? f.stacks : 0
}
const freezePct = (sprite) => {
  if (!sprite?.max_hp) return 0
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
  if (action.type === 'item') return '道具'
  return action.type
}
</script>

<template>
  <div class="flex flex-col" style="min-height: calc(100vh - 49px)">

    <!-- Top Bar -->
    <div class="bg-white border-b border-[#D4C8B8] px-4 py-1.5 flex items-center gap-4 text-xs">
      <span class="font-bold text-[#3D2B1F]">回合 {{ store.turn }}</span>
      <span class="text-[#6B5E4F]">|</span>
      <span class="text-[#6B5E4F]">
        {{ store.weather ? `天气: ${store.weather} (${store.weatherTurns}t)` : '无天气' }}
      </span>
      <span v-if="store.isFinished" class="ml-auto font-bold text-[#D4534A]">
        已结束 &mdash; {{ store.winner }} 获胜
      </span>
      <span v-else class="ml-auto text-[#6B5E4F]">进行中</span>
    </div>

    <!-- Battle Field -->
    <div class="flex-1 flex flex-col lg:flex-row">

      <!-- Player Side -->
      <div class="flex-1 flex flex-col border-b lg:border-b-0 lg:border-r border-[#D4C8B8]">
        <!-- Active Sprite -->
        <div class="flex-1 flex flex-col items-center justify-center p-6">
          <div class="text-center mb-4">
            <div class="text-lg font-bold text-[#3D2B1F]">
              {{ active?.name }}
              <span v-if="active?.is_fainted" class="text-[#D4534A] text-xs ml-1">(力竭)</span>
              <span v-if="isCharging" class="text-[#C9A96E] text-xs ml-1">蓄力-{{ active?.charging }}</span>
            </div>
            <div class="text-[10px] text-[#6B5E4F] mt-0.5">Lv.100</div>
          </div>

          <!-- SpriteCard -->
          <SpriteCard
            :sprite="active || {}"
            :is-fainted="active?.is_fainted ?? false"
          />

          <!-- HP Bar -->
          <div class="w-64 mb-3">
            <div class="flex justify-between text-[10px] text-[#6B5E4F] mb-0.5">
              <span>HP</span>
              <span>{{ active?.current_hp ?? 0 }} / {{ active?.max_hp ?? 0 }}</span>
            </div>
            <div class="w-full h-3.5 bg-[#F5E6D3] rounded-sm overflow-hidden border border-[#D4C8B8] relative">
              <div
                class="h-full transition-all duration-400"
                :class="active ? hpColorClass(active.current_hp, active.max_hp) : ''"
                :style="{ width: hpPct(active?.current_hp ?? 0, active?.max_hp ?? 0) + '%' }"
              ></div>
              <!-- Freeze overlay -->
              <div
                v-if="active && freezeStacks(active) > 0"
                class="absolute top-0 left-0 h-full bg-gradient-to-r from-[#7ec8e3]/80 via-[#a0d8ef]/60 to-[#c8e8f8]/30 transition-all duration-400 rounded-sm"
                :style="{ width: Math.min(freezePct(active), hpPct(active.current_hp, active.max_hp)) + '%' }"
              ></div>
            </div>
          </div>

          <!-- Energy -->
          <div class="flex items-center gap-1 mb-3">
            <span class="text-[10px] text-[#6B5E4F] mr-1">能量</span>
            <div class="flex gap-0.5">
              <div
                v-for="i in 10"
                :key="i"
                class="w-2.5 h-4 rounded-sm transition-colors duration-300"
                :class="i <= (active?.energy ?? 0) ? 'bg-gradient-to-b from-[#C9A96E] to-[#A08050]' : 'bg-[#D4C8B8]'"
              ></div>
            </div>
            <span class="text-[10px] text-[#6B5E4F] ml-1">{{ active?.energy ?? 0 }}/10</span>
          </div>

          <!-- Effects -->
          <div v-if="active?.effects && active.effects.length > 0" class="flex flex-wrap gap-1 justify-center">
            <EffectCard
              v-for="(eff, effIdx) in active.effects"
              :key="effIdx"
              :effect="eff"
            />
          </div>
        </div>

        <!-- Marks Bar -->
        <div v-if="store.marksA && store.marksA.length > 0" class="px-4 pb-2 flex flex-wrap gap-1">
          <span
            v-for="(m, mi) in store.marksA"
            :key="mi"
            class="px-1.5 py-0.5 text-[10px] rounded border"
            :class="m.type === 'positive'
              ? 'bg-[#E8F5E9] border-[#A5D6A7] text-[#5C8D6E]'
              : 'bg-[#EDE7F6] border-[#B39DDB] text-[#7B4F9D]'"
          >
            [{{ m.name }}<template v-if="m.stacks > 1"> &times;{{ m.stacks }}</template>]
          </span>
        </div>

        <!-- Action Panel -->
        <div v-if="!store.isFinished" class="border-t border-[#D4C8B8] p-3 bg-[#F5F2EC]">
          <template v-if="!showSwitchMenu">
            <!-- Skill Buttons -->
            <div :class="['grid gap-1.5 mb-1.5', gridCols]">
              <SkillButton
                v-for="sk in active?.skills || []"
                :key="sk.name"
                :skill="sk"
                :skill-meta="props.skillMap[sk.name]"
                :disabled="(active?.is_fainted ?? true) || !canUseSkill(active, sk.name)"
                :energy-insufficient="energyInsufficient(active, sk.name)"
                :selected="debugMode && debugActionA?.payload === sk.name"
                @select="(name) => handleAction('skill', name)"
              />
            </div>
            <!-- Utility Buttons -->
            <div class="flex gap-1.5">
              <button
                @click="handleAction('gather')"
                :disabled="(active?.is_fainted ?? true) || (isCharging && !hasJealousy)"
                class="flex-1 bg-white hover:bg-[#F5F2EC] disabled:opacity-40 border text-xs font-medium py-2 rounded transition-colors"
                :class="[
                  debugMode && debugActionA?.type === 'gather'
                    ? 'border-[#5C8D6E] text-[#5C8D6E] ring-1 ring-[#5C8D6E]/30'
                    : 'border-[#D4C8B8] text-[#6B5E4F] hover:border-[#C9A96E]'
                ]"
              >
                聚能
              </button>
              <button
                v-if="player.item"
                @click="handleAction('item')"
                :disabled="(active?.is_fainted ?? true) || player.item.is_exhausted || !canUseItem"
                class="group relative flex-1 border text-xs font-medium py-2 rounded transition-colors disabled:opacity-40"
                :class="[
                  debugMode && debugActionA?.type === 'item' ? '!border-[#5C8D6E] ring-1 ring-[#5C8D6E]/30' : '',
                  player.item.is_exhausted
                    ? 'bg-[#E8E0D5] border-[#D4C8B8] text-[#B0A595] cursor-not-allowed'
                    : 'bg-[#FFF3E0] border-[#EF6C00]/40 hover:border-[#EF6C00] text-[#EF6C00]'
                ]"
              >
                <div class="flex items-center justify-center gap-1">
                  <span>{{ player.item.name }}</span>
                  <span class="text-[10px] text-[#6B5E4F]">({{ player.item.max_uses - player.item.uses }})</span>
                </div>
                <!-- Tooltip -->
                <div class="absolute bottom-full left-1/2 -translate-x-1/2 mb-2 px-3 py-2 bg-[#3D2B1F] border border-[#C9A96E]/40 text-[#FBF7F0] text-xs rounded shadow-lg whitespace-nowrap opacity-0 group-hover:opacity-100 transition-opacity pointer-events-none z-50">
                  <template v-if="player.item.is_exhausted">已用完</template>
                  <template v-else-if="player.item.last_use_turn > 0 && store.turn - player.item.last_use_turn < player.item.cooldown_turns">
                    冷却中({{ player.item.cooldown_turns - (store.turn - player.item.last_use_turn) }}回合后可用)
                  </template>
                  <template v-else>使用道具</template>
                </div>
              </button>
              <button
                @click="showSwitchMenu = true"
                class="flex-1 bg-white hover:bg-[#F5F2EC] border border-[#D4C8B8] hover:border-[#C9A96E] text-[#6B5E4F] text-xs font-medium py-2 rounded transition-colors"
              >
                换宠
              </button>
            </div>
            <!-- Debug Status -->
            <div v-if="debugMode" class="mt-2 flex items-center gap-2 text-[10px]">
              <span class="px-2 py-0.5 rounded" :class="debugActionA ? 'bg-[#E8F5E9] text-[#5C8D6E] border border-[#5C8D6E]/50' : 'bg-[#FFEBEE] text-[#D4534A] border border-[#D4534A]/50'">
                我方: {{ debugActionLabel(debugActionA) }}
              </span>
              <span class="text-[#6B5E4F]">|</span>
              <span class="px-2 py-0.5 rounded" :class="debugActionB ? 'bg-[#FFF3E0] text-[#EF6C00] border border-[#EF6C00]/50' : 'bg-[#FFEBEE] text-[#D4534A] border border-[#D4534A]/50'">
                对方: {{ debugActionLabel(debugActionB) }}
              </span>
            </div>
          </template>

          <!-- Switch Menu -->
          <template v-else>
            <div class="text-xs text-[#6B5E4F] mb-2 text-center">选择替换上场:</div>
            <div class="grid grid-cols-3 gap-1.5 mb-1.5">
              <button
                v-for="(sprite, i) in store.selfTeam"
                :key="i"
                @click="handleAction('switch', i); showSwitchMenu = false"
                :disabled="i === store.activeIndexA || sprite.is_fainted"
                class="bg-white hover:bg-[#F5F2EC] disabled:opacity-40 border text-left px-2.5 py-2 rounded transition-colors"
                :class="i === store.activeIndexA ? 'border-[#5C8D6E]' : 'border-[#D4C8B8] hover:border-[#6B5E4F]'"
              >
                <div class="text-xs font-bold text-[#3D2B1F] truncate">{{ sprite.name }}</div>
                <div class="text-[10px]" :class="sprite.is_fainted ? 'text-[#D4534A]' : 'text-[#6DBF7C]'">
                  {{ sprite.is_fainted ? '力竭' : `${sprite.current_hp} HP` }}
                </div>
              </button>
            </div>
            <button
              @click="showSwitchMenu = false"
              class="w-full bg-[#E8E0D5] hover:bg-[#D4C8B8] text-[#6B5E4F] text-xs py-1.5 rounded transition-colors"
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
            <div class="text-lg font-bold text-[#3D2B1F]">
              {{ activeOpp?.name }}
              <span v-if="activeOpp?.is_fainted" class="text-[#D4534A] text-xs ml-1">(力竭)</span>
              <span v-if="isChargingOpp" class="text-[#C9A96E] text-xs ml-1">蓄力-{{ activeOpp?.charging }}</span>
            </div>
            <div class="text-[10px] text-[#6B5E4F] mt-0.5">Lv.100</div>
          </div>

          <!-- SpriteCard -->
          <SpriteCard
            :sprite="activeOpp || {}"
            :is-fainted="activeOpp?.is_fainted ?? false"
          />

          <!-- HP Bar -->
          <div class="w-64 mb-3">
            <div class="flex justify-between text-[10px] text-[#6B5E4F] mb-0.5">
              <span>HP</span>
              <span>{{ activeOpp?.current_hp ?? 0 }} / {{ activeOpp?.max_hp ?? 0 }}</span>
            </div>
            <div class="w-full h-3.5 bg-[#F5E6D3] rounded-sm overflow-hidden border border-[#D4C8B8] relative">
              <div
                class="h-full transition-all duration-400"
                :class="activeOpp ? hpColorClass(activeOpp.current_hp, activeOpp.max_hp) : ''"
                :style="{ width: hpPct(activeOpp?.current_hp ?? 0, activeOpp?.max_hp ?? 0) + '%' }"
              ></div>
              <!-- Freeze overlay -->
              <div
                v-if="activeOpp && freezeStacks(activeOpp) > 0"
                class="absolute top-0 left-0 h-full bg-gradient-to-r from-[#7ec8e3]/80 via-[#a0d8ef]/60 to-[#c8e8f8]/30 transition-all duration-400 rounded-sm"
                :style="{ width: Math.min(freezePct(activeOpp), hpPct(activeOpp.current_hp, activeOpp.max_hp)) + '%' }"
              ></div>
            </div>
          </div>

          <!-- Energy -->
          <div class="flex items-center gap-1 mb-3">
            <span class="text-[10px] text-[#6B5E4F] mr-1">能量</span>
            <div class="flex gap-0.5">
              <div
                v-for="i in 10"
                :key="i"
                class="w-2.5 h-4 rounded-sm transition-colors duration-300"
                :class="i <= (activeOpp?.energy ?? 0) ? 'bg-gradient-to-b from-[#C9A96E] to-[#A08050]' : 'bg-[#D4C8B8]'"
              ></div>
            </div>
            <span class="text-[10px] text-[#6B5E4F] ml-1">{{ activeOpp?.energy ?? 0 }}/10</span>
          </div>

          <!-- Effects -->
          <div v-if="activeOpp?.effects && activeOpp.effects.length > 0" class="flex flex-wrap gap-1 justify-center">
            <EffectCard
              v-for="(eff, effIdx) in activeOpp.effects"
              :key="effIdx"
              :effect="eff"
            />
          </div>
        </div>

        <!-- Marks Bar -->
        <div v-if="store.marksB && store.marksB.length > 0" class="px-4 pb-2 flex flex-wrap gap-1">
          <span
            v-for="(m, mi) in store.marksB"
            :key="mi"
            class="px-1.5 py-0.5 text-[10px] rounded border"
            :class="m.type === 'positive'
              ? 'bg-[#E8F5E9] border-[#A5D6A7] text-[#5C8D6E]'
              : 'bg-[#EDE7F6] border-[#B39DDB] text-[#7B4F9D]'"
          >
            [{{ m.name }}<template v-if="m.stacks > 1"> &times;{{ m.stacks }}</template>]
          </span>
        </div>

        <!-- Opponent: Debug Action Panel -->
        <div v-if="debugMode && !store.isFinished" class="border-t border-[#EF6C00]/50 p-3 bg-[#F5F2EC]">
          <div class="text-[10px] text-[#EF6C00] mb-1 font-bold">[调试] 对手操作</div>
          <template v-if="!showSwitchMenuOpp">
            <div :class="['grid gap-1.5 mb-1.5', gridColsOpp]">
              <SkillButton
                v-for="sk in activeOpp?.skills || []"
                :key="sk.name"
                :skill="sk"
                :skill-meta="props.skillMap[sk.name]"
                :disabled="(activeOpp?.is_fainted ?? true) || !canUseSkill(activeOpp, sk.name)"
                :energy-insufficient="energyInsufficient(activeOpp, sk.name)"
                :selected="debugActionB?.payload === sk.name"
                @select="(name) => selectDebugAction('B', 'skill', name)"
              />
            </div>
            <div class="flex gap-1.5">
              <button
                @click="selectDebugAction('B', 'gather')"
                :disabled="activeOpp?.is_fainted ?? true"
                class="flex-1 bg-white hover:bg-[#F5F2EC] disabled:opacity-40 border text-xs font-medium py-2 rounded transition-colors"
                :class="debugActionB?.type === 'gather' ? 'border-[#EF6C00] text-[#EF6C00]' : 'border-[#D4C8B8] text-[#6B5E4F]'"
              >
                聚能
              </button>
              <button
                @click="showSwitchMenuOpp = true"
                class="flex-1 bg-white hover:bg-[#F5F2EC] border border-[#D4C8B8] hover:border-[#EF6C00] text-[#6B5E4F] text-xs font-medium py-2 rounded transition-colors"
              >
                换宠
              </button>
            </div>
          </template>
          <template v-else>
            <div class="text-xs text-[#6B5E4F] mb-2 text-center">选择对手替换上场:</div>
            <div class="grid grid-cols-3 gap-1.5 mb-1.5">
              <button
                v-for="(sprite, i) in store.oppTeam"
                :key="i"
                @click="selectDebugAction('B', 'switch', i); showSwitchMenuOpp = false"
                :disabled="i === store.activeIndexB || sprite.is_fainted"
                class="bg-white hover:bg-[#F5F2EC] disabled:opacity-40 border text-left px-2.5 py-2 rounded transition-colors"
                :class="i === store.activeIndexB ? 'border-[#5C8D6E]' : 'border-[#D4C8B8] hover:border-[#6B5E4F]'"
              >
                <div class="text-xs font-bold text-[#3D2B1F] truncate">{{ sprite.name }}</div>
                <div class="text-[10px]" :class="sprite.is_fainted ? 'text-[#D4534A]' : 'text-[#6DBF7C]'">
                  {{ sprite.is_fainted ? '力竭' : `${sprite.current_hp} HP` }}
                </div>
              </button>
            </div>
            <button
              @click="showSwitchMenuOpp = false"
              class="w-full bg-[#E8E0D5] hover:bg-[#D4C8B8] text-[#6B5E4F] text-xs py-1.5 rounded transition-colors"
            >
              取消
            </button>
          </template>
        </div>

        <!-- Opponent: Normal Mini-view -->
        <div v-else class="border-t border-[#D4C8B8] p-3 bg-[#F5F2EC]">
          <div class="grid grid-cols-3 gap-1.5">
            <div
              v-for="(sprite, i) in store.oppTeam"
              :key="i"
              class="bg-white border rounded px-2 py-1.5 text-center"
              :class="i === store.activeIndexB ? 'border-[#5C8D6E]' : 'border-[#D4C8B8]'"
            >
              <div class="text-[10px] font-bold text-[#3D2B1F] truncate">{{ sprite.name }}</div>
              <div class="text-[10px]" :class="sprite.is_fainted ? 'text-[#D4534A]' : 'text-[#6DBF7C]'">
                {{ sprite.is_fainted ? '力竭' : Math.round((sprite.current_hp / sprite.max_hp) * 100) + '%' }}
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>
