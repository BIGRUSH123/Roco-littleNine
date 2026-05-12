<script setup>
import { ref, onMounted } from 'vue'
import TeamSelection from './components/TeamSelection.vue'
import BattleArena from './components/BattleArena.vue'
import BattleLog from './components/BattleLog.vue'

const API_BASE = 'http://localhost:8000/api'

const currentPhase = ref('selection')
const battleState = ref(null)
const battleLogs = ref([])
const isProcessing = ref(false)
const battleError = ref('')
const skillMap = ref({})
const typeChart = ref({})

onMounted(async () => {
  try {
    const [skillsRes, chartRes] = await Promise.all([
      fetch(`${API_BASE}/skills`),
      fetch(`${API_BASE}/type-chart`)
    ])
    if (skillsRes.ok) {
      const data = await skillsRes.json()
      skillMap.value = data.skills || {}
    }
    if (chartRes.ok) {
      const data = await chartRes.json()
      typeChart.value = data.chart || {}
    }
  } catch (e) {
    console.error('Failed to load data:', e)
  }
})

const TEST_OPPONENT = [
  { name: '双灯鱼', skills: ['水炮', '求雨'] },
  { name: '大头骨龙', skills: ['龙吟', '龙威'] },
  { name: '嗜光嗡嗡', skills: ['毒针', '吹火'] },
  { name: '黑羽夫人', skills: ['乘风', '恶意逃离'] },
]

const handleStartBattle = async ({ team, leadIndex }) => {
  try {
    isProcessing.value = true
    const res = await fetch(`${API_BASE}/battle/init`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ team, opponent_team: TEST_OPPONENT, lead_index: leadIndex })
    })

    if (!res.ok) throw new Error('战斗初始化失败')

    const data = await res.json()
    battleState.value = data
    battleLogs.value = ['战斗开始！']
    currentPhase.value = 'battle'
  } catch (error) {
    console.error('Error starting battle:', error)
    alert('初始化战斗失败，请确认后端已启动。')
  } finally {
    isProcessing.value = false
  }
}

const handleAction = async ({ type, payload }) => {
  if (isProcessing.value || battleState.value.is_finished) return

  try {
    isProcessing.value = true

    const reqBody = {
      session_id: battleState.value.session_id,
      action_type: type
    }

    if (type === 'skill') reqBody.skill_name = payload
    if (type === 'switch') reqBody.switch_index = payload

    const res = await fetch(`${API_BASE}/battle/action`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(reqBody)
    })

    if (!res.ok) throw new Error('操作执行失败')

    const data = await res.json()
    battleState.value = data.state

    if (data.log && data.log.length > 0) {
      battleLogs.value.push(...data.log)
    }

  } catch (error) {
    console.error('Error executing action:', error)
    battleError.value = error.message || '操作失败'
    setTimeout(() => { battleError.value = '' }, 4000)
  } finally {
    isProcessing.value = false
  }
}

const restartGame = () => {
  currentPhase.value = 'selection'
  battleState.value = null
  battleLogs.value = []
}
</script>

<template>
  <div class="min-h-screen bg-[#1a1d23] text-[#cdd6e0]">

    <!-- Header -->
    <header class="bg-[#252830] border-b border-[#3a3d42] px-4 py-3 flex items-center gap-3">
      <div class="w-5 h-5 rounded-sm bg-[#4a90d9]"></div>
      <h1 class="text-base font-bold tracking-wide text-[#e0e0e0]">
        格斗小九
      </h1>
      <span class="text-xs text-[#6a6d75] ml-auto">v0.1</span>
    </header>

    <!-- Main Content -->
    <main class="mx-auto" style="max-width: 1100px">

      <Transition name="fade" mode="out-in">
        <TeamSelection
          v-if="currentPhase === 'selection'"
          :skill-map="skillMap"
          @start-battle="handleStartBattle"
        />

        <div v-else-if="currentPhase === 'battle' && battleState" class="flex flex-col lg:flex-row gap-0 lg:gap-0">

          <!-- Error Toast -->
          <Transition name="fade">
            <div v-if="battleError" class="fixed top-12 left-1/2 -translate-x-1/2 z-50 bg-[#3a1a1a] border border-[#f44336] text-[#f44336] text-xs px-4 py-2 rounded shadow-lg">
              {{ battleError }}
            </div>
          </Transition>

          <!-- Battle Arena (left) -->
          <div class="flex-1 min-w-0">
            <BattleArena
              :state="battleState"
              :skill-map="skillMap"
              :type-chart="typeChart"
              @action="handleAction"
              :class="{'opacity-50 pointer-events-none': isProcessing}"
            />
          </div>

          <!-- Battle Log (right sidebar) -->
          <div class="lg:w-72 xl:w-80 flex-shrink-0">
            <BattleLog :logs="battleLogs" />
          </div>

        </div>
      </Transition>

    </main>

    <!-- Game Over Overlay -->
    <div v-if="battleState?.is_finished" class="fixed inset-0 bg-black/70 flex items-center justify-center z-50">
      <div class="bg-[#252830] p-10 rounded border border-[#3a3d42] text-center shadow-lg">
        <div class="text-xs text-[#6a6d75] tracking-widest uppercase mb-3">战斗结果</div>
        <h2 class="text-3xl font-bold mb-2" :class="battleState.winner === '玩家' ? 'text-[#4caf50]' : 'text-[#f44336]'">
          {{ battleState.winner === '玩家' ? '胜利' : '失败' }}
        </h2>
        <p class="text-sm text-[#9a9da5] mb-8">{{ battleState.winner }} 赢得了战斗！</p>
        <button
          @click="restartGame"
          class="px-8 py-2.5 bg-[#4a90d9] hover:bg-[#5a9fe9] text-white text-sm font-bold rounded transition-colors"
        >
          再来一局
        </button>
      </div>
    </div>

  </div>
</template>

<style>
.fade-enter-active,
.fade-leave-active {
  transition: opacity 0.15s ease;
}

.fade-enter-from,
.fade-leave-to {
  opacity: 0;
}
</style>
