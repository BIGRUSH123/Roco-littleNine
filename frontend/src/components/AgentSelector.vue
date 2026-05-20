<script setup>
import { ref, onMounted, watch } from 'vue'

const props = defineProps({
  modelValue: { type: String, default: '' },
})

const emit = defineEmits(['update:modelValue'])

const agents = ref([])
const loading = ref(true)
const error = ref('')

const API_BASE = '/api'

async function loadAgents() {
  loading.value = true
  error.value = ''
  try {
    const res = await fetch(`${API_BASE}/agents`)
    if (!res.ok) throw new Error(`服务器返回 ${res.status}`)
    const data = await res.json()
    agents.value = data.agents || []
  } catch (e) {
    error.value = e.message || '无法加载 AI 对手列表'
    agents.value = []
  } finally {
    loading.value = false
  }
}

function selectAgent(name) {
  emit('update:modelValue', name)
}

onMounted(loadAgents)
</script>

<template>
  <div class="agent-selector">
    <label class="block text-sm font-medium text-parchment-700 mb-2">
      智能对手
      <span v-if="loading" class="text-moss-500 ml-1">加载中...</span>
    </label>

    <!-- Error state -->
    <div v-if="error" class="text-fire-600 text-xs mb-2 p-2 bg-fire-50 rounded-lg border border-fire-200">
      {{ error }}
      <button
        class="ml-2 underline hover:text-fire-800"
        @click="loadAgents"
      >重试</button>
    </div>

    <!-- Empty state -->
    <div
      v-if="!loading && !error && agents.length === 0"
      class="text-xs text-parchment-500 p-3 bg-parchment-50 rounded-lg border border-parchment-200"
    >
      暂无可用的 AI 对手。将使用默认 AI。
    </div>

    <!-- Agent grid -->
    <div
      v-if="!loading && agents.length > 0"
      class="grid grid-cols-1 gap-2 max-h-48 overflow-y-auto"
    >
      <button
        v-for="agent in agents"
        :key="agent.name"
        type="button"
        class="agent-card text-left p-3 rounded-xl border-2 transition-all duration-200"
        :class="modelValue === agent.name
          ? 'border-gold-500 bg-gold-50 shadow-gold-glow'
          : 'border-parchment-200 bg-white/60 hover:border-moss-300 hover:shadow-card'"
        @click="selectAgent(agent.name)"
      >
        <div class="flex items-center justify-between">
          <span class="font-medium text-sm text-parchment-900">{{ agent.name }}</span>
          <span
            v-if="agent.source === 'builtin'"
            class="text-[10px] px-2 py-0.5 rounded-full bg-moss-100 text-moss-700"
          >内置</span>
          <span
            v-else-if="agent.source === 'example'"
            class="text-[10px] px-2 py-0.5 rounded-full bg-purple-100 text-purple-700"
          >示例</span>
          <span
            v-else-if="agent.source === 'demo'"
            class="text-[10px] px-2 py-0.5 rounded-full bg-gold-100 text-gold-700"
          >Demo</span>
        </div>
        <p class="text-xs text-parchment-600 mt-1">{{ agent.description }}</p>
      </button>
    </div>
  </div>
</template>

<style scoped>
.agent-card {
  cursor: pointer;
}
.agent-card:hover {
  transform: translateY(-1px);
}
</style>
