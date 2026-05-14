<script setup>
import { ref, watch, nextTick, computed } from 'vue'

const props = defineProps({
  logs: { type: Array, required: true }
})

const logContainer = ref(null)

watch(() => props.logs, async () => {
  await nextTick()
  if (logContainer.value) {
    logContainer.value.scrollTop = logContainer.value.scrollHeight
  }
}, { deep: true })

function isTurnHeader(text) {
  return /^\[回合\d+\]/.test(text)
}

function formatLog(text) {
  let html = text
    .replace(/(\d+) 点伤害/g, '<b class="text-[#f44336]">$1</b> 点伤害')
    .replace(/(\d+)点伤害/g, '<b class="text-[#f44336]">$1</b>点伤害')
    .replace(/(-?\d+)HP/g, '<b class="text-[#f44336]">$1</b>HP')
    .replace(/回复 (\d+) 点HP/g, '回复 <b class="text-[#4caf50]">$1</b> 点HP')
    .replace(/力竭/g, '<b class="text-[#f44336]">力竭</b>')
    // Turn header: highlight turn number
    .replace(/^\[(回合\d+)\](.*)/, '<span class="text-[#4a90d9] font-bold">[$1]</span>$2')
    .replace(/\[([^\]]+)\]/g, '<span class="text-[#ffc107]">[$1]</span>')

  // 应对/打断日志高亮
  if (/应对/.test(text) || /打断/.test(text) || /反击/.test(text)) {
    html = '<span class="text-[#ffa726]">' + html + '</span>'
  }

  return html
}
</script>

<template>
  <div class="bg-[#1e2128] border-l border-[#3a3d42] flex flex-col overflow-hidden" style="height:0;min-height:100%">
    <!-- Log Header -->
    <div class="px-3 py-2 border-b border-[#3a3d42]">
      <span class="text-xs font-bold text-[#e0e0e0]">战斗日志</span>
    </div>

    <!-- Log Entries -->
    <div
      ref="logContainer"
      class="flex-1 overflow-y-auto p-2 space-y-1"
    >
      <div v-if="logs.length === 0" class="text-xs text-[#5a5d65] italic p-2">
        等待战斗开始...
      </div>

      <div
        v-for="(log, idx) in logs"
        :key="idx"
        class="text-xs leading-relaxed px-2 py-1 rounded font-mono"
        :class="isTurnHeader(log)
          ? 'text-[#e0e0e0] bg-[#2a3040] border-l-2 border-[#4a90d9] font-bold mt-2 first:mt-0'
          : 'text-[#9a9da5] hover:bg-[#252830]'"
        v-html="formatLog(log)"
      ></div>
    </div>
  </div>
</template>
