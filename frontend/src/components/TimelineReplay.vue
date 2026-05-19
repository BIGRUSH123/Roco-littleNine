<script setup>
import { ref, computed, watch, onUnmounted } from 'vue'
import { Dialog, DialogPanel, DialogTitle } from '@headlessui/vue'
import { useBattleStore } from '../stores/battle.js'

const props = defineProps({
  isOpen: { type: Boolean, default: false },
})

const emit = defineEmits(['close'])

const battle = useBattleStore()

const isAutoPlaying = ref(false)
const currentReplayTurn = ref(0)
let autoPlayTimer = null

const isActive = computed(() => props.isOpen)

const snapshots = computed(() => battle.turnSnapshots)
const maxTurn = computed(() => {
  if (snapshots.value.length > 0) return snapshots.value[snapshots.value.length - 1].turn
  return battle.turn
})

const logEntries = computed(() => {
  const snap = snapshots.value.find(s => s.turn === currentReplayTurn.value)
  return snap?.log_entries || []
})

function seekTo(turn) {
  currentReplayTurn.value = Math.max(0, Math.min(turn, maxTurn.value))
  battle.setReplayTurn(currentReplayTurn.value)
}

function stepBack() { seekTo(currentReplayTurn.value - 1) }
function stepForward() { seekTo(currentReplayTurn.value + 1) }

function toggleAutoPlay() {
  if (isAutoPlaying.value) { stopAutoPlay() } else { startAutoPlay() }
}

function startAutoPlay() {
  isAutoPlaying.value = true
  autoPlayTimer = setInterval(() => {
    if (currentReplayTurn.value >= maxTurn.value) { stopAutoPlay(); return }
    seekTo(currentReplayTurn.value + 1)
  }, 1500)
}

function stopAutoPlay() {
  isAutoPlaying.value = false
  if (autoPlayTimer) { clearInterval(autoPlayTimer); autoPlayTimer = null }
}

function close() {
  stopAutoPlay()
  battle.replayMode = false
  emit('close')
}

onUnmounted(() => { stopAutoPlay() })
</script>

<template>
  <Dialog :open="isActive" @close="close" class="relative z-50">
    <div class="fixed inset-0 bg-[#3D2B1F]/60" aria-hidden="true" />

    <div class="fixed inset-0 flex items-center justify-center p-4">
      <DialogPanel class="w-full max-w-2xl bg-[#FBF7F0] rounded-2xl border border-[#D4C8B8] shadow-xl overflow-hidden">
        <!-- Header -->
        <div class="px-6 py-4 border-b border-[#D4C8B8] flex items-center justify-between">
          <DialogTitle class="text-lg font-bold text-[#3D2B1F] font-[family-name:var(--font-title)]">
            ⏸ 回合时间线回放
          </DialogTitle>
          <button @click="close" class="text-[#6B5E4F] hover:text-[#3D2B1F] text-xl leading-none">&times;</button>
        </div>

        <!-- Timeline -->
        <div class="px-6 py-6">
          <div class="text-center mb-4">
            <span class="text-2xl font-bold text-[#3D2B1F] font-[family-name:var(--font-title)]">回合 {{ currentReplayTurn }}</span>
            <span class="text-sm text-[#6B5E4F] ml-2">/ {{ maxTurn }}</span>
          </div>

          <!-- Track with dots -->
          <div class="relative h-16 flex items-center">
            <div class="absolute left-0 right-0 h-1 bg-[#D4C8B8] rounded-full"></div>
            <div
              class="absolute left-0 h-1 bg-[#C9A96E] rounded-full transition-all duration-300"
              :style="{ width: maxTurn > 0 ? (currentReplayTurn / maxTurn * 100) + '%' : '0%' }"
            ></div>

            <div class="absolute left-0 right-0 px-2" style="top: 50%; transform: translateY(-50%);">
              <div class="relative w-full">
                <button
                  v-for="snap in snapshots"
                  :key="snap.turn"
                  @click="seekTo(snap.turn)"
                  :class="[
                    'absolute w-3 h-3 rounded-full transition-all -translate-x-1/2',
                    snap.turn <= currentReplayTurn ? 'bg-[#C9A96E] scale-110' : 'bg-[#D4C8B8] hover:bg-[#C9A96E]/60',
                    snap.turn === currentReplayTurn ? 'pulse-dot scale-125' : ''
                  ]"
                  :style="{ left: maxTurn > 0 ? (snap.turn / maxTurn * 100) + '%' : '0%' }"
                  :title="'回合 ' + snap.turn"
                ></button>
              </div>
            </div>
          </div>

          <!-- Controls -->
          <div class="flex items-center justify-center gap-3 mt-6">
            <button @click="stepBack" :disabled="currentReplayTurn <= 0"
              class="w-10 h-10 rounded-full bg-[#5C8D6E] text-white flex items-center justify-center text-lg disabled:opacity-30 disabled:cursor-not-allowed hover:bg-[#4A7D5E] transition-colors">◀</button>
            <button @click="toggleAutoPlay"
              :class="['px-5 py-2 rounded-xl font-bold text-sm transition-colors', isAutoPlaying ? 'bg-[#D4534A] text-white hover:bg-[#C62828]' : 'bg-[#C9A96E] text-white hover:bg-[#B0985D]']">
              {{ isAutoPlaying ? '⏸ 停止' : '▶ 自动播放' }}
            </button>
            <button @click="stepForward" :disabled="currentReplayTurn >= maxTurn"
              class="w-10 h-10 rounded-full bg-[#5C8D6E] text-white flex items-center justify-center text-lg disabled:opacity-30 disabled:cursor-not-allowed hover:bg-[#4A7D5E] transition-colors">▶</button>
          </div>

          <!-- Snapshot summary -->
          <div v-if="snapshots.find(s => s.turn === currentReplayTurn)" class="mt-6 p-4 bg-[#F0EDE5] rounded-xl border border-[#D4C8B8]">
            <div class="text-xs font-bold text-[#3D2B1F] mb-2">回合 {{ currentReplayTurn }} 详情</div>
            <div class="grid grid-cols-2 gap-3 text-xs text-[#6B5E4F]">
              <div>
                <span class="font-bold">我方 HP:</span>
                {{ snapshots.find(s => s.turn === currentReplayTurn)?.self_sprite?.current_hp || '?' }} /
                {{ snapshots.find(s => s.turn === currentReplayTurn)?.self_sprite?.max_hp || '?' }}
              </div>
              <div>
                <span class="font-bold">对方 HP:</span>
                {{ snapshots.find(s => s.turn === currentReplayTurn)?.opp_sprite?.current_hp || '?' }} /
                {{ snapshots.find(s => s.turn === currentReplayTurn)?.opp_sprite?.max_hp || '?' }}
              </div>
            </div>
            <!-- Log entries for this turn -->
            <div v-if="logEntries.length > 0" class="mt-3 space-y-0.5">
              <div v-for="(entry, i) in logEntries" :key="i" class="text-xs text-[#6B5E4F]">{{ entry }}</div>
            </div>
          </div>
        </div>
      </DialogPanel>
    </div>
  </Dialog>
</template>
