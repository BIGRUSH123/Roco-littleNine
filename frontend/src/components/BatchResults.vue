<script setup>
import { ref } from 'vue'

const props = defineProps({
  team: { type: Array, default: () => [] },
  selectedAgent: { type: String, default: 'RuleAgent' },
})

const emit = defineEmits(['close'])

const running = ref(false)
const result = ref(null)
const error = ref('')
const rounds = ref(10)

const API_BASE = '/api'

async function runBatch() {
  if (!props.team.length) {
    error.value = '请先配置队伍'
    return
  }

  running.value = true
  error.value = ''
  result.value = null

  try {
    const teamData = props.team.map(s => ({
      name: s.name,
      skills: s.skills,
      bloodline: s.bloodline || undefined,
      form: s.form || '',
    }))

    const res = await fetch(`${API_BASE}/battle/batch`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ team: teamData, ai_agent: props.selectedAgent, rounds: rounds.value }),
    })
    if (!res.ok) {
      const err = await res.json()
      throw new Error(err.detail || `服务器错误 (${res.status})`)
    }
    result.value = await res.json()
  } catch (e) {
    error.value = e.message || '批处理失败 — 请确认后端已启动'
  } finally {
    running.value = false
  }
}
</script>

<template>
  <div class="batch-results card bg-white rounded-xl p-5">
    <div class="flex items-center justify-between mb-4">
      <h3 class="text font-bold text-[#3D2B1F]">批量对战</h3>
      <button @click="emit('close')" class="text-[#6B5E4F] hover:text-[#D4534A] text-lg">&times;</button>
    </div>

    <!-- Config -->
    <div class="flex items-center gap-3 mb-4">
      <label class="text-xs text-[#6B5E4F]">轮数:</label>
      <select
        v-model.number="rounds"
        class="bg-[#F5F2EC] border border-[#D4C8B8] rounded-lg px-2 py-1 text-sm text-[#3D2B1F]"
      >
        <option :value="3">3 (快速)</option>
        <option :value="10">10</option>
        <option :value="20">20</option>
        <option :value="50">50</option>
      </select>
      <span class="text-[10px] text-[#6B5E4F]">AI: {{ selectedAgent }}</span>
      <button
        :disabled="running"
        @click="runBatch"
        :class="[
          'ml-auto px-4 py-1.5 text-xs font-bold rounded-lg transition-colors',
          running
            ? 'bg-[#EDE8DF] text-[#A89A8A] cursor-not-allowed'
            : 'btn btn-primary text-white'
        ]"
      >
        {{ running ? '运行中...' : '开始' }}
      </button>
    </div>

    <!-- Error -->
    <div v-if="error" class="text-xs text-[#D4534A] mb-3 p-2 bg-[#FFF3E0] rounded-lg">
      {{ error }}
    </div>

    <!-- Results -->
    <div v-if="result" class="space-y-3">
      <div class="grid grid-cols-3 gap-3 text-center">
        <div class="bg-[#F0F7F0] rounded-lg p-3">
          <div class="text-2xl font-bold text-[#5C8D6E]">{{ result.wins }}</div>
          <div class="text-[10px] text-[#6B5E4F]">胜</div>
        </div>
        <div class="bg-[#FFF3E0] rounded-lg p-3">
          <div class="text-2xl font-bold text-[#D4534A]">{{ result.losses }}</div>
          <div class="text-[10px] text-[#6B5E4F]">负</div>
        </div>
        <div class="bg-[#F5F2EC] rounded-lg p-3">
          <div class="text-2xl font-bold text-[#6B5E4F]">{{ result.draws }}</div>
          <div class="text-[10px] text-[#6B5E4F]">平</div>
        </div>
      </div>

      <!-- Win rate bar -->
      <div class="bg-[#F5F2EC] rounded-full h-3 overflow-hidden flex">
        <div
          class="bg-[#5C8D6E] h-full transition-all duration-500"
          :style="{ width: (result.win_rate * 100) + '%' }"
        />
        <div
          class="bg-[#D4534A] h-full transition-all duration-500"
          :style="{ width: (result.losses / result.rounds * 100) + '%' }"
        />
        <div
          class="bg-[#C9A96E] h-full transition-all duration-500"
          :style="{ width: (result.draws / result.rounds * 100) + '%' }"
        />
      </div>

      <div class="text-xs text-[#6B5E4F] space-y-1">
        <div class="flex justify-between">
          <span>胜率</span>
          <span class="font-bold">{{ (result.win_rate * 100).toFixed(1) }}%</span>
        </div>
        <div class="flex justify-between">
          <span>平均回合</span>
          <span>{{ result.avg_turns.toFixed(1) }}t</span>
        </div>
        <div class="flex justify-between">
          <span>平均耗时</span>
          <span>{{ result.avg_duration_ms.toFixed(0) }}ms</span>
        </div>
      </div>
    </div>
  </div>
</template>
