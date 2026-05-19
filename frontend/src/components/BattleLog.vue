<script setup>
import { ref, watch, nextTick } from 'vue'

const props = defineProps({
  logs: { type: Array, required: true },
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

function formatLogClass(text) {
  if (/伤害|扣血|HP\s*[-−]/.test(text)) return 'text-[#D4534A]'
  if (/回复|治疗|HP\s*\+/.test(text)) return 'text-[#6DBF7C]'
  if (/中毒|灼烧|冻结|麻痹|睡眠|异常|诅咒|寄生/.test(text)) return 'text-[#4A3B5C]'
  if (/打断|应对|反击/.test(text)) return 'text-[#EF6C00]'
  if (/力竭/.test(text)) return 'text-[#D4534A] font-bold'
  if (/增益|提升|增加/.test(text)) return 'text-[#5C8D6E]'
  return 'text-[#6B5E4F]'
}

function formatLogHtml(text) {
  return text
    .replace(/(\d+)\s*点伤害/g, '<b class="text-[#D4534A]">$1点伤害</b>')
    .replace(/回复\s*(\d+)\s*点HP/g, '回复 <b class="text-[#6DBF7C]">$1点HP</b>')
    .replace(/力竭/g, '<b class="text-[#D4534A]">力竭</b>')
    .replace(/^\[(回合\d+)\](.*)/, '<span class="text-[#C9A96E] font-bold">[$1]</span>$2')
}
</script>

<template>
  <div class="bg-[#FBF7F0] border-l border-[#D4C8B8] flex flex-col overflow-hidden h-full rounded-r-2xl">
    <div class="px-4 py-3 border-b border-[#D4C8B8]">
      <span class="text-sm font-bold text-[#3D2B1F] font-[family-name:var(--font-title)]">战斗日志</span>
    </div>

    <div ref="logContainer" class="flex-1 overflow-y-auto p-3 space-y-1.5">
      <div v-if="logs.length === 0" class="text-xs text-[#B0A595] italic p-3">
        等待战斗开始...
      </div>

      <div
        v-for="(log, idx) in logs"
        :key="idx"
        class="text-xs leading-relaxed px-3 py-1.5 rounded-lg font-mono"
        :class="[
          formatLogClass(log),
          isTurnHeader(log)
            ? 'bg-[#F0EDE5] border-l-2 border-[#C9A96E] font-bold mt-2 first:mt-0'
            : 'hover:bg-[#F5F2EC]'
        ]"
        v-html="formatLogHtml(log)"
      ></div>
    </div>
  </div>
</template>
