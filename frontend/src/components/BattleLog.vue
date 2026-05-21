<script setup>
import { ref, watch, nextTick, computed } from 'vue'

const props = defineProps({
  logs: { type: Array, required: true },
})

const logContainer = ref(null)
const collapsedTurns = ref(new Set())

watch(() => props.logs, async () => {
  await nextTick()
  if (logContainer.value) {
    logContainer.value.scrollTop = logContainer.value.scrollHeight
  }
}, { deep: true })

function toggleTurn(turn) {
  if (collapsedTurns.value.has(turn)) {
    collapsedTurns.value.delete(turn)
  } else {
    collapsedTurns.value.add(turn)
  }
  // Trigger reactivity
  collapsedTurns.value = new Set(collapsedTurns.value)
}

// Parse flat log into structured sections
const sections = computed(() => {
  try {
    const raw = props.logs || []
    const result = []
    let currentTurn = null
    let currentAction = null
    let currentPhase = null  // 'turnStart' | 'turnEnd' | null

    for (let i = 0; i < raw.length; i++) {
      const line = raw[i]
      if (typeof line !== 'string') {
        console.warn('[BattleLog] non-string log entry:', line)
        continue
      }

      // Turn header: [回合N] ...
      const turnMatch = line.match(/^\[回合(\d+)\]\s*(.*)/)
      if (turnMatch) {
        if (currentAction) { currentAction = null }
        currentPhase = null
        currentTurn = {
          turn: parseInt(turnMatch[1]),
          header: turnMatch[2],
          sprites: '',
          actions: [],
          turnStartEffects: [],
          turnEndEffects: [],
        }
        result.push(currentTurn)
        continue
      }

      // Sprites marker: >>>SPRITES:nameA|nameB
      const spritesMatch = line.match(/^>>>SPRITES:(.+)/)
      if (spritesMatch && currentTurn) {
        currentTurn.sprites = spritesMatch[1]
        continue
      }

      // Item marker: >>>ITEM:team:name
      const itemMatch = line.match(/^>>>ITEM:(.+?):(.+)/)
      if (itemMatch && currentTurn) {
        currentTurn.itemUsed = { team: itemMatch[1], name: itemMatch[2] }
        continue
      }

      // Phase start: >>>PHASE:NAME
      const phaseMatch = line.match(/^>>>PHASE:(.+)/)
      if (phaseMatch && currentTurn) {
        const name = phaseMatch[1]
        if (name === 'TURN_START') currentPhase = 'turnStart'
        else if (name === 'TURN_END') currentPhase = 'turnEnd'
        continue
      }

      // Phase end: <<<PHASE
      if (line === '<<<PHASE') {
        currentPhase = null
        continue
      }

      // Action start: >>>ACTION:actor:skill
      const actionMatch = line.match(/^>>>ACTION:(.+?):(.+)/)
      if (actionMatch && currentTurn) {
        currentPhase = null
        currentAction = {
          actor: actionMatch[1],
          skill: actionMatch[2],
          effects: [],
        }
        currentTurn.actions.push(currentAction)
        continue
      }

      // Action end: <<<ACTION
      if (line === '<<<ACTION') {
        currentAction = null
        continue
      }

      // Place effect in the right bucket
      if (currentAction) {
        currentAction.effects.push(line)
      } else if (currentTurn && currentPhase === 'turnStart') {
        currentTurn.turnStartEffects.push(line)
      } else if (currentTurn && currentPhase === 'turnEnd') {
        currentTurn.turnEndEffects.push(line)
      }
    }

    if (result.length > 0) {
      console.log('[BattleLog] parsed', result.length, 'turns, total lines:', raw.length)
    }
    return result
  } catch (e) {
    console.error('[BattleLog] parse error:', e)
    return []
  }
})

function effectClass(text) {
  if (/伤害|扣血|[-−]\d+HP/.test(text)) return 'text-[#D4534A]'
  if (/回复|治疗|\+HP/.test(text)) return 'text-[#6DBF7C]'
  if (/中毒|灼烧|冻结|麻痹|睡眠|异常|诅咒|寄生/.test(text)) return 'text-[#4A3B5C]'
  if (/打断|应对|反击/.test(text)) return 'text-[#EF6C00]'
  if (/力竭/.test(text)) return 'text-[#D4534A] font-bold'
  if (/增益|提升|增加/.test(text)) return 'text-[#5C8D6E]'
  return 'text-[#6B5E4F]'
}

function skillIcon(skill) {
  if (skill === '聚能') return '◆'
  if (skill.startsWith('换宠→')) return '↻'
  return '▶'
}
</script>

<template>
  <div class="bg-[#FBF7F0] border-l border-[#D4C8B8] flex flex-col overflow-hidden h-full rounded-r-2xl">
    <div class="px-4 py-3 border-b border-[#D4C8B8] flex items-center justify-between">
      <span class="text-sm font-bold text-[#3D2B1F] font-[family-name:var(--font-title)]">战斗日志</span>
      <span v-if="sections.length" class="text-[10px] text-[#A89A8A]">{{ sections.length }}回合</span>
    </div>

    <div ref="logContainer" class="flex-1 overflow-y-auto p-2 space-y-1">
      <div v-if="logs.length === 0" class="text-xs text-[#B0A595] italic p-3">
        等待战斗开始...
      </div>

      <!-- Fallback: raw lines when no structured sections found -->
      <div v-if="sections.length === 0 && logs.length > 0" class="space-y-1">
        <div v-for="(line, idx) in logs" :key="'raw'+idx" class="text-xs px-3 py-1.5 rounded-lg font-mono"
          :class="effectClass(line)">
          {{ line }}
        </div>
      </div>

      <div v-for="(turn, ti) in sections" :key="ti" class="rounded-lg overflow-hidden">
        <!-- Turn Header (always visible, clickable to collapse) -->
        <button
          @click="toggleTurn(turn.turn)"
          class="w-full text-left px-3 py-2 bg-[#F0EDE5] border-l-2 border-[#C9A96E] flex items-center gap-2 hover:bg-[#EBE5D8] transition-colors"
        >
          <span class="text-[10px] text-[#A89A8A] transition-transform" :class="collapsedTurns.has(turn.turn) ? 'rotate-0' : 'rotate-90'">▶</span>
          <span class="text-xs font-bold text-[#C9A96E] font-mono">[回合{{ turn.turn }}]</span>
          <span class="text-xs text-[#3D2B1F] truncate flex-1">{{ turn.header }}</span>
          <span class="text-[10px] text-[#A89A8A]">{{ turn.actions.length }}行动</span>
        </button>

        <!-- Collapsible body -->
        <div v-if="!collapsedTurns.has(turn.turn)" class="px-2 py-1 space-y-1 bg-[#FBF7F0] border-l-2 border-transparent">
          <!-- Turn start phase (weather, transmission, etc.) -->
          <template v-if="turn.turnStartEffects.length">
            <div class="text-[10px] text-[#A89A8A] pl-4 pt-1 font-medium">⚡ 回合开始</div>
            <div v-for="(ef, ei) in turn.turnStartEffects" :key="'ts'+ei" class="text-[11px] pl-6 py-0.5"
              :class="effectClass(ef)">
              {{ ef }}
            </div>
          </template>

          <!-- Item usage -->
          <div v-if="turn.itemUsed" class="text-[11px] pl-6 py-0.5 text-[#C9A96E] font-medium">
            🧪 {{ turn.itemUsed.name }}
          </div>

          <!-- Actions -->
          <div v-for="(action, ai) in turn.actions" :key="'act'+ai" class="rounded-lg overflow-hidden">
            <div class="text-[11px] font-bold pl-4 py-1 text-[#5C8D6E] flex items-center gap-1.5">
              <span>{{ skillIcon(action.skill) }}</span>
              <span class="text-[#3D2B1F]">{{ action.actor }}</span>
              <span class="text-[#6B5E4F] font-normal">{{ action.skill }}</span>
            </div>
            <!-- Action effects -->
            <div class="pl-6 space-y-0.5 border-l border-[#E8E0D4] ml-4">
              <div v-for="(ef, ei) in action.effects" :key="'ef'+ei" class="text-[11px] py-0.5"
                :class="effectClass(ef)">
                {{ ef }}
              </div>
            </div>
          </div>

          <!-- Turn end phase (dot, weather damage, etc.) -->
          <template v-if="turn.turnEndEffects.length">
            <div class="text-[10px] text-[#A89A8A] pl-4 pt-1 font-medium">⏳ 回合结束</div>
            <div v-for="(ef, ei) in turn.turnEndEffects" :key="'te'+ei" class="text-[11px] pl-6 py-0.5"
              :class="effectClass(ef)">
              {{ ef }}
            </div>
          </template>
        </div>
      </div>
    </div>
  </div>
</template>
