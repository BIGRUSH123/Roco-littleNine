<script setup>
import { ref, onMounted } from 'vue'
import { useBattleStore } from './stores/battle.js'
import { useSpriteAssetStore } from './stores/spriteAssets.js'
import TeamSelection from './components/TeamSelection.vue'
import BattleArena from './components/BattleArena.vue'
import BattleLog from './components/BattleLog.vue'
import TypeChart from './components/TypeChart.vue'
import TimelineReplay from './components/TimelineReplay.vue'
import ExportDialog from './components/ExportDialog.vue'

const battleStore = useBattleStore()
const spriteAssets = useSpriteAssetStore()

const API_BASE = '/api'

const currentPhase = ref('selection')
const battleState = ref(null)
const battleLogs = ref([])
const isProcessing = ref(false)
const battleError = ref('')
const skillMap = ref({})
const typeChart = ref({})
const debugMode = ref(false)
const showReplay = ref(false)
const showExport = ref(false)
const showBacktrack = ref(false)
const exportMessage = ref('')

onMounted(async () => {
  try {
    const [skillsRes, chartRes] = await Promise.all([
      fetch(`${API_BASE}/skills`),
      fetch(`${API_BASE}/type-chart`)
    ])
    if (skillsRes.ok) {
      const data = await skillsRes.json()
      skillMap.value = data.skills || {}
    } else {
      console.error('Skills load failed:', skillsRes.status)
    }
    if (chartRes.ok) {
      const data = await chartRes.json()
      typeChart.value = data.chart || {}
    } else {
      console.error('Type chart load failed:', chartRes.status)
    }
  } catch (e) {
    console.error('Failed to load data:', e)
  }
})

const TEST_TEAM = [
  { name: '棋契陛下', skills: ['冰锋横扫', '钢钻', '回旋踢', '三连破', '丰饶', '虫群智慧', '不可接触', '充分燃烧'] },
  { name: '迪莫', skills: ['闪击', '冥想', '主场优势', '晒太阳', '生日蛋糕', '龙吟'] },
  { name: '卡洛儿', skills: ['勾魂', '假寐', '冬至', '冰冻光线', '击鼓传花', '远程访问', '借用', '加大功率'] },
  { name: '游蛇魔使', skills: ['水炮', '听桥', '潮汐', '打湿', '休息回复', '冰墙', '力量增效', '丢冰块'] },
  { name: '黑羽夫人', skills: ['恶意逃离', '恶念交换', '欺诈契约', '隐藏条款', '落井下毒', '以毒攻毒', '偷袭'] },
  { name: '大头骨龙', skills: ['龙吟', '三鼓作气', '破绽', '硬门', '冰点', '杠杆置换', '根吸收'] },
]

const TEST_OPPONENT = [
  { name: '双灯鱼', skills: ['水炮', '求雨', '水刃', '潮汐', '休息回复', '冰冻光线'] },
  { name: '大头骨龙', skills: ['龙吟', '龙威', '三鼓作气', '硬门', '崩拳', '破绽'] },
  { name: '游蛇魔使', skills: ['听桥', '打湿', '冰墙', '力量增效', '暗箱操作', '等价交换'] },
  { name: '黑羽夫人', skills: ['恶意逃离', '偷袭', '落井下毒', '乘风', '突袭'] },
]

const handleQuickTest = async () => {
  try {
    isProcessing.value = true
    const res = await fetch(`${API_BASE}/battle/init`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ team: TEST_TEAM, opponent_team: TEST_OPPONENT, lead_index: 0 })
    })
    if (!res.ok) {
      const errText = await res.text()
      throw new Error(`HTTP ${res.status}: ${errText}`)
    }
    const data = await res.json()
    battleState.value = data
    battleStore.updateFromResponse(data)
    battleLogs.value = ['快速测试开始！']
    spriteAssets.preloadTeam([...TEST_TEAM.map(s => s.name), ...TEST_OPPONENT.map(s => s.name)])
    currentPhase.value = 'battle'
  } catch (error) {
    console.error('Error starting battle:', error)
    alert(`初始化战斗失败: ${error.message}\n\n请确认后端已启动 (python scripts/api/main.py)`)
  } finally {
    isProcessing.value = false
  }
}

const handleStartBattle = async ({ team, leadIndex, item, aiAgent }) => {
  try {
    isProcessing.value = true
    const res = await fetch(`${API_BASE}/battle/init`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ team, opponent_team: TEST_OPPONENT, lead_index: leadIndex, item: item || undefined, ai_agent: aiAgent || 'RuleAgent' })
    })

    if (!res.ok) {
      const errText = await res.text()
      throw new Error(`HTTP ${res.status}: ${errText}`)
    }

    const data = await res.json()
    battleState.value = data
    battleStore.updateFromResponse(data)
    battleLogs.value = ['战斗开始！']
    spriteAssets.preloadTeam([...team.map(s => s.name), ...TEST_OPPONENT.map(s => s.name)])
    currentPhase.value = 'battle'
  } catch (error) {
    console.error('Error starting battle:', error)
    alert(`初始化战斗失败: ${error.message}\n\n请确认后端已启动 (python scripts/api/main.py)`)
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

    if (!res.ok) throw new Error(`操作执行失败 (${res.status})`)

    const data = await res.json()
    battleState.value = data.state
    battleStore.updateFromResponse(data.state)

    if (data.log && data.log.length > 0) {
      battleLogs.value.push(...data.log)
    }

  } catch (error) {
    console.error('Error executing action:', error)
    battleError.value = error.message || '操作失败 — 请重试'
    setTimeout(() => { battleError.value = '' }, 4000)
  } finally {
    isProcessing.value = false
  }
}

const handleResolveEscape = async ({ switch_index }) => {
  if (isProcessing.value) return

  try {
    isProcessing.value = true

    const res = await fetch(`${API_BASE}/battle/resolve-escape`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        session_id: battleState.value.session_id,
        switch_index: switch_index,
      })
    })

    if (!res.ok) {
      const errData = await res.json().catch(() => ({}))
      throw new Error(errData.detail || `脱离失败 (${res.status})`)
    }

    const data = await res.json()
    battleState.value = data.state
    battleStore.updateFromResponse(data.state)

    if (data.log && data.log.length > 0) {
      battleLogs.value.push(...data.log)
    }

  } catch (error) {
    console.error('Error resolving escape:', error)
    battleError.value = error.message || '脱离失败'
    setTimeout(() => { battleError.value = '' }, 4000)
  } finally {
    isProcessing.value = false
  }
}

const handleDebugInit = async () => {
  try {
    isProcessing.value = true
    const res = await fetch(`${API_BASE}/debug/init`, { method: 'POST' })
    if (!res.ok) {
      const errText = await res.text()
      throw new Error(`HTTP ${res.status}: ${errText}`)
    }
    const data = await res.json()
    battleState.value = data
    battleStore.updateFromResponse(data)
    battleLogs.value = [`[调试模式] 双方准备就绪 | 我方 ${data.debug_skills_a?.length || 0} 技能 | 对方 ${data.debug_skills_b?.length || 0} 技能`]
    if (data.player_a?.team) spriteAssets.preloadTeam(data.player_a.team.map(s => s.name))
    if (data.player_b?.team) spriteAssets.preloadTeam(data.player_b.team.map(s => s.name))
    currentPhase.value = 'battle'
    debugMode.value = true
  } catch (error) {
    console.error('Debug init failed:', error)
    alert(`调试模式初始化失败: ${error.message}\n\n请确认后端已启动 (python scripts/api/main.py)`)
  } finally {
    isProcessing.value = false
  }
}

const handleDebugAction = async ({ actionA, actionB }) => {
  if (isProcessing.value || battleState.value.is_finished) return

  try {
    isProcessing.value = true

    const res = await fetch(`${API_BASE}/debug/action`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        session_id: battleState.value.session_id,
        action_a: actionA,
        action_b: actionB,
      })
    })

    if (!res.ok) throw new Error(`操作执行失败 (${res.status})`)

    const data = await res.json()
    battleState.value = data.state
    battleStore.updateFromResponse(data.state)

    if (data.log && data.log.length > 0) {
      battleLogs.value.push(...data.log)
    }
  } catch (error) {
    console.error('Debug action failed:', error)
    battleError.value = error.message || '操作失败 — 请重试'
    setTimeout(() => { battleError.value = '' }, 4000)
  } finally {
    isProcessing.value = false
  }
}

const handleRestore = async (turn) => {
  if (isProcessing.value || !battleState.value) return
  try {
    isProcessing.value = true
    const res = await fetch(`${API_BASE}/battle/restore`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        session_id: battleState.value.session_id,
        turn: turn,
      })
    })
    if (!res.ok) {
      const err = await res.json().catch(() => ({}))
      throw new Error(err.detail || `回溯失败 (${res.status})`)
    }
    const data = await res.json()
    battleState.value = data.state
    battleStore.resetBattle()
    battleStore.updateFromResponse(data.state)
    battleLogs.value = [`◀ 已回溯至第 ${data.restored_turn} 回合`]
    showBacktrack.value = false
  } catch (e) {
    battleError.value = e.message || '回溯失败'
    setTimeout(() => { battleError.value = '' }, 4000)
  } finally {
    isProcessing.value = false
  }
}

const handleExportMatch = async ({ name }) => {
  if (!battleState.value) return
  try {
    const res = await fetch(`${API_BASE}/battle/export`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        session_id: battleState.value.session_id,
        name: name,
      })
    })
    if (!res.ok) {
      const err = await res.json().catch(() => ({}))
      throw new Error(err.detail || `导出失败 (${res.status})`)
    }
    const data = await res.json()
    exportMessage.value = `已导出: ${data.name}`
    setTimeout(() => { exportMessage.value = '' }, 3000)
  } catch (e) {
    battleError.value = e.message || '导出失败'
    setTimeout(() => { battleError.value = '' }, 4000)
  }
}

const handleImportMatch = async (name) => {
  try {
    isProcessing.value = true
    const res = await fetch(`${API_BASE}/battle/import`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name })
    })
    if (!res.ok) {
      const err = await res.json().catch(() => ({}))
      throw new Error(err.detail || `导入失败 (${res.status})`)
    }
    const data = await res.json()
    battleState.value = data
    battleStore.resetBattle()
    battleStore.updateFromResponse(data)
    battleLogs.value = [`已导入对局: ${name}`]
    currentPhase.value = 'battle'
    showExport.value = false
  } catch (e) {
    battleError.value = e.message || '导入失败'
    setTimeout(() => { battleError.value = '' }, 4000)
  } finally {
    isProcessing.value = false
  }
}

const restartGame = () => {
  currentPhase.value = 'selection'
  battleState.value = null
  battleLogs.value = []
  debugMode.value = false
  showReplay.value = false
  battleStore.resetBattle()
}
</script>

<template>
  <div class="min-h-screen relative" style="background: var(--bg-main); z-index: 1;">

    <!-- Header -->
    <header class="border-b px-5 py-3 flex items-center gap-3" style="background: linear-gradient(180deg, rgba(251,247,240,0.98) 0%, rgba(245,240,230,0.95) 100%); border-color: var(--border-default);">
      <div class="text-xl font-[family-name:var(--font-title)] font-bold text-[#3D2B1F] tracking-wide">
        ✦ 格斗小九 ✦
      </div>

      <template v-if="currentPhase === 'selection'">
        <button
          @click="handleQuickTest"
          :disabled="isProcessing"
          class="ml-auto px-4 py-1.5 bg-[#5C8D6E] hover:bg-[#4A7D5E] disabled:opacity-40 text-white text-xs font-bold rounded-xl transition-colors mr-2 shadow-[0_2px_0_rgba(61,43,31,0.15)]"
        >
          快速测试
        </button>
        <button
          @click="handleDebugInit"
          :disabled="isProcessing"
          class="px-4 py-1.5 bg-[#EF6C00] hover:bg-[#F57C00] disabled:opacity-40 text-white text-xs font-bold rounded-xl transition-colors mr-2 shadow-[0_2px_0_rgba(61,43,31,0.15)]"
        >
          调试模式
        </button>
        <span class="text-xs text-[#B0A595]">v0.2</span>
      </template>

      <template v-if="currentPhase === 'battle'">
        <span class="text-sm text-[#6B5E4F] font-[family-name:var(--font-title)]">回合 {{ battleState?.turn || 0 }}/150</span>
        <span class="text-[#D4C8B8]">|</span>
        <span class="text-sm text-[#6B5E4F]">
          {{ battleState?.weather ? `天气: ${battleState.weather}` : '无天气' }}
        </span>
        <!-- Backtrack Button -->
        <div class="relative">
          <button
            @click="showBacktrack = !showBacktrack"
            :disabled="battleStore.availableSnapshotTurns.length === 0"
            class="px-3 py-1.5 bg-[#7E57C2] hover:bg-[#6A4DAB] disabled:opacity-30 disabled:cursor-not-allowed text-white text-xs font-bold rounded-xl transition-colors mr-1 shadow-[0_2px_0_rgba(61,43,31,0.15)]"
            title="回溯到之前回合"
          >
            ◀ 回溯
          </button>
          <!-- Backtrack Dropdown -->
          <Transition name="fade">
            <div v-if="showBacktrack" class="absolute top-full right-0 mt-1 bg-white border border-[#D4C8B8] rounded-xl shadow-xl z-40 min-w-[140px] py-1">
              <div class="px-3 py-1.5 text-[10px] text-[#B0A595] font-bold tracking-wide">选择回溯回合</div>
              <button
                v-for="t in battleStore.availableSnapshotTurns"
                :key="t"
                @click="handleRestore(t)"
                :disabled="isProcessing"
                class="w-full text-left px-3 py-1.5 text-sm text-[#3D2B1F] hover:bg-[#F5F0E6] disabled:opacity-40 transition-colors"
              >
                回合 {{ t }}
                <span v-if="t === battleState?.turn" class="text-[10px] text-[#B0A595] ml-1">(当前)</span>
              </button>
              <div v-if="battleStore.availableSnapshotTurns.length === 0" class="px-3 py-2 text-xs text-[#B0A595]">
                暂无可回溯回合
              </div>
            </div>
          </Transition>
        </div>
        <!-- Backtrack backdrop (click outside to close) -->
        <div v-if="showBacktrack" class="fixed inset-0 z-30" @click="showBacktrack = false"></div>

        <!-- Export Button -->
        <button
          @click="showExport = true"
          class="px-3 py-1.5 bg-[#5C8D6E] hover:bg-[#4A7D5E] text-white text-xs font-bold rounded-xl transition-colors mr-1 shadow-[0_2px_0_rgba(61,43,31,0.15)]"
        >
          导出/导入
        </button>

        <button
          @click="showReplay = true"
          :disabled="battleStore.turnSnapshots.length === 0"
          class="px-3 py-1.5 bg-[#C9A96E] hover:bg-[#B0985D] disabled:opacity-30 disabled:cursor-not-allowed text-white text-xs font-bold rounded-xl transition-colors mr-2 shadow-[0_2px_0_rgba(61,43,31,0.15)]"
        >
          回放
        </button>
        <button
          v-if="debugMode"
          @click="restartGame"
          class="px-4 py-1.5 bg-[#D4534A] hover:bg-[#C62828] text-white text-xs font-bold rounded-xl transition-colors mr-2 shadow-[0_2px_0_rgba(61,43,31,0.15)]"
        >
          退出调试
        </button>
      </template>
    </header>

    <!-- Main Content -->
    <main class="mx-auto" style="max-width: 1200px">

      <Transition name="page" mode="out-in">
        <TeamSelection
          v-if="currentPhase === 'selection'"
          :skill-map="skillMap"
          @start-battle="handleStartBattle"
        />

        <div v-else-if="currentPhase === 'battle' && battleState" class="flex flex-col lg:flex-row gap-0">

          <!-- Error Toast -->
          <Transition name="fade">
            <div v-if="battleError" class="fixed top-16 left-1/2 -translate-x-1/2 z-50 bg-[#FFF3E0] border border-[#D4534A] text-[#D4534A] text-xs px-4 py-2 rounded-xl shadow-lg">
              {{ battleError }}
            </div>
          </Transition>

          <!-- Battle Arena -->
          <div class="flex-1 min-w-0">
            <BattleArena
              :skill-map="skillMap"
              :type-chart="typeChart"
              :debug-mode="debugMode"
              @action="handleAction"
              @debug-action="handleDebugAction"
              @resolve-escape="handleResolveEscape"
              :class="{'opacity-50 pointer-events-none': isProcessing}"
            />
          </div>

          <!-- Right Sidebar: Log (70%) + Type Chart (30%) -->
          <div class="lg:w-80 xl:w-96 flex-shrink-0 flex flex-col border-l border-[#D4C8B8] min-h-0">
            <div class="flex-[7] min-h-0 overflow-y-auto">
              <BattleLog :logs="battleLogs" />
            </div>
            <div class="flex-[3] border-t border-[#D4C8B8] overflow-y-auto">
              <TypeChart :type-chart="typeChart" />
            </div>
          </div>

        </div>
      </Transition>

    </main>

    <!-- Timeline Replay Dialog -->
    <TimelineReplay :is-open="showReplay" @close="showReplay = false" />

    <!-- Export Dialog -->
    <ExportDialog
      :is-open="showExport"
      :api-base="API_BASE"
      :current-turn="battleState?.turn || 0"
      @close="showExport = false"
      @exported="handleExportMatch"
      @imported="handleImportMatch"
    />

    <!-- Export Success Toast -->
    <Transition name="fade">
      <div v-if="exportMessage" class="fixed top-16 left-1/2 -translate-x-1/2 z-50 bg-[#F0F7F0] border border-[#5C8D6E] text-[#5C8D6E] text-xs px-4 py-2 rounded-xl shadow-lg">
        {{ exportMessage }}
      </div>
    </Transition>

    <!-- Game Over Overlay -->
    <div v-if="battleState?.is_finished" class="fixed inset-0 bg-[#3D2B1F]/60 flex items-center justify-center z-50">
      <div class="card p-10 text-center shadow-xl max-w-sm w-full mx-4">
        <div class="text-xs text-[#B0A595] tracking-widest uppercase mb-3">战斗结果</div>
        <h2 class="text-3xl font-bold mb-2 font-[family-name:var(--font-title)]"
            :class="battleState.winner === '玩家' ? 'text-[#5C8D6E]' : 'text-[#D4534A]'">
          {{ battleState.winner === '玩家' ? '胜利 ✦' : '失败' }}
        </h2>
        <p class="text-sm text-[#6B5E4F] mb-8">{{ battleState.winner }} 赢得了战斗！</p>
        <button
          @click="restartGame"
          class="btn btn-primary px-8 py-2.5 text-sm"
        >
          再来一局
        </button>
      </div>
    </div>

  </div>
</template>

<style>
.page-enter-active,
.page-leave-active {
  transition: opacity 0.2s ease, transform 0.2s ease;
}
.page-enter-from {
  opacity: 0;
  transform: translateY(8px);
}
.page-leave-to {
  opacity: 0;
  transform: translateY(-8px);
}

.fade-enter-active,
.fade-leave-active {
  transition: opacity 0.15s ease;
}
.fade-enter-from,
.fade-leave-to {
  opacity: 0;
}
</style>
