<script setup>
import { ref, watch } from 'vue'

const props = defineProps({
  isOpen: { type: Boolean, default: false },
  apiBase: { type: String, default: '/api' },
  currentTurn: { type: Number, default: 0 },
})

const emit = defineEmits(['close', 'exported', 'imported'])

const exportName = ref('')
const exports = ref([])
const isLoading = ref(false)
const error = ref('')

watch(() => props.isOpen, (open) => {
  if (open) loadExports()
})

async function loadExports() {
  isLoading.value = true
  error.value = ''
  try {
    const res = await fetch(`${props.apiBase}/exports`)
    if (!res.ok) throw new Error(`HTTP ${res.status}`)
    const data = await res.json()
    exports.value = data.exports || []
  } catch (e) {
    error.value = '加载导出列表失败'
  } finally {
    isLoading.value = false
  }
}

function handleExport() {
  if (!exportName.value.trim()) return
  emit('exported', { name: exportName.value.trim() })
  exportName.value = ''
}

function handleImport(name) {
  emit('imported', name)
}

function formatSize(bytes) {
  if (bytes < 1024) return `${bytes}B`
  return `${(bytes / 1024).toFixed(1)}KB`
}

</script>

<template>
  <Teleport to="body">
    <Transition name="fade">
      <div v-if="isOpen" class="fixed inset-0 bg-[#3D2B1F]/50 flex items-center justify-center z-50" @click.self="$emit('close')">
        <div class="card p-6 w-full max-w-md mx-4 shadow-xl" style="background: var(--bg-card);">
          <div class="flex items-center justify-between mb-5">
            <h3 class="text-lg font-bold font-[family-name:var(--font-title)] text-[#3D2B1F]">导出 / 导入</h3>
            <button @click="$emit('close')" class="text-[#B0A595] hover:text-[#6B5E4F] text-xl leading-none">&times;</button>
          </div>

          <!-- Error -->
          <div v-if="error" class="mb-4 px-3 py-2 bg-[#FFF3E0] border border-[#D4534A]/20 rounded-lg text-xs text-[#D4534A]">
            {{ error }}
          </div>

          <!-- Export Section -->
          <div class="mb-5">
            <label class="block text-xs font-bold text-[#6B5E4F] mb-2 tracking-wide">导出当前对局</label>
            <div class="flex gap-2">
              <input
                v-model="exportName"
                type="text"
                placeholder="输入导出名称..."
                class="flex-1 px-3 py-2 text-sm rounded-lg border border-[#D4C8B8] bg-[#FBF7F0] text-[#3D2B1F] focus:outline-none focus:border-[#5C8D6E]"
                @keyup.enter="handleExport"
              />
              <button
                @click="handleExport"
                :disabled="!exportName.trim()"
                class="px-4 py-2 bg-[#5C8D6E] hover:bg-[#4A7D5E] disabled:opacity-40 text-white text-xs font-bold rounded-xl transition-colors"
              >
                导出
              </button>
            </div>
            <p class="text-[10px] text-[#B0A595] mt-1">回合 {{ currentTurn }} · JSON + 文本双格式</p>
          </div>

          <!-- Imports List -->
          <div>
            <div class="flex items-center justify-between mb-2">
              <label class="text-xs font-bold text-[#6B5E4F] tracking-wide">已导出的文件</label>
              <button
                @click="loadExports"
                :disabled="isLoading"
                class="text-[10px] text-[#5C8D6E] hover:text-[#4A7D5E] font-bold"
              >
                {{ isLoading ? '加载中...' : '刷新' }}
              </button>
            </div>

            <div v-if="exports.length === 0" class="text-xs text-[#B0A595] py-4 text-center">
              暂无导出文件
            </div>

            <div v-else class="max-h-48 overflow-y-auto space-y-1">
              <div
                v-for="exp in exports.filter(e => e.format === 'json')"
                :key="exp.name"
                class="flex items-center justify-between px-3 py-2 rounded-lg hover:bg-[#F5F0E6] transition-colors"
              >
                <div class="flex-1 min-w-0">
                  <div class="text-sm text-[#3D2B1F] font-medium truncate">{{ exp.stem }}</div>
                  <div class="text-[10px] text-[#B0A595]">
                    {{ exp.type === 'match' ? '对局' : '队伍' }} &middot; {{ formatSize(exp.size) }}
                  </div>
                </div>
                <button
                  v-if="exp.type === 'match'"
                  @click="handleImport(exp.stem)"
                  class="ml-2 px-3 py-1 bg-[#C9A96E] hover:bg-[#B0985D] text-white text-[10px] font-bold rounded-lg transition-colors"
                >
                  导入
                </button>
              </div>
            </div>
          </div>
        </div>
      </div>
    </Transition>
  </Teleport>
</template>
