<script setup>
import { computed, ref, onMounted, onUnmounted } from 'vue'
import { useSpriteAssetStore } from '../stores/spriteAssets.js'
import { useSpriteAnim } from '../composables/useSpriteAnim.js'

const ELEMENTAL = ['普通','火','水','草','电','冰','地','石','武','虫','翼','萌','毒','幽','恶','幻','光','龙','机械']

const props = defineProps({
  sprite: { type: Object, default: null },
  size: { type: String, default: 'md' },
  showHp: { type: Boolean, default: false },
  showEnergy: { type: Boolean, default: false },
  isFainted: { type: Boolean, default: false },
  showBloodline: { type: Boolean, default: false },
})

const spriteAssets = useSpriteAssetStore()

const imageKey = computed(() => props.sprite?.image_key || props.sprite?.name || '')

const spriteUrl = computed(() => {
  if (!imageKey.value) return null
  return spriteAssets.getUrl(imageKey.value)
})

const hasError = ref(false)
function onImageError() {
  hasError.value = true
  spriteAssets.setError(imageKey.value)
}

const spriteEl = ref(null)
const shadowEl = ref(null)
const { playIdle, cleanup } = useSpriteAnim(spriteEl, shadowEl)

onMounted(() => {
  if (!props.isFainted) playIdle()
})

onUnmounted(() => {
  cleanup()
})

const sizeClass = computed(() => ({
  sm: 'w-20 h-20',
  md: 'w-32 h-32',
  lg: 'w-48 h-48',
})[props.size])

const hpPct = computed(() => {
  if (!props.sprite) return 0
  const { current_hp, max_hp } = props.sprite
  return max_hp > 0 ? Math.max(0, Math.min(100, (current_hp / max_hp) * 100)) : 0
})

const primaryElement = computed(() => {
  return props.sprite?.element?.split(',')[0]?.trim() || ''
})

const bloodlineClass = computed(() => {
  const bl = props.sprite?.bloodline || ''
  if (!bl) return ''
  if (ELEMENTAL.includes(bl)) {
    const map = { '石': '石' }
    return `element-${map[bl] || bl}`
  }
  return `bloodline-${bl}`
})
</script>

<template>
  <div class="flex flex-col items-center gap-2" :class="{ 'opacity-60 grayscale': isFainted }">
    <div
      ref="spriteEl"
      :class="[sizeClass, 'relative rounded-full flex items-center justify-center']"
      :style="{ background: primaryElement ? `var(--elem-color, #C9A96E)` : '#C9A96E' }"
    >
      <img
        v-if="spriteUrl && !hasError"
        :src="spriteUrl"
        :alt="sprite?.name || '精灵'"
        class="w-full h-full object-contain p-1"
        @error="onImageError"
      />
      <span
        v-else
        class="text-white text-sm font-bold text-center px-2 leading-tight"
      >{{ sprite?.name || '???' }}</span>

      <div v-if="isFainted" class="absolute inset-0 bg-black/40 rounded-full flex items-center justify-center">
        <span class="text-[#D4534A] text-xs font-bold">力竭</span>
      </div>
    </div>

    <div ref="shadowEl" class="sprite-shadow"></div>

    <span
      v-if="showBloodline && sprite?.bloodline"
      :class="['elem-tag', bloodlineClass]"
    >{{ sprite.bloodline }}</span>

    <div v-if="showHp && sprite" class="w-full max-w-40">
      <div class="flex justify-between text-xs text-[#6B5E4F] mb-0.5">
        <span>HP</span>
        <span>{{ sprite.current_hp }}/{{ sprite.max_hp }}</span>
      </div>
      <div class="w-full h-3 bg-[#E8E0D5] rounded-full overflow-hidden border border-[#D4C8B8]">
        <div
          class="h-full transition-all duration-500 rounded-full"
          :style="{ width: hpPct + '%' }"
          :class="hpPct > 50 ? 'bg-gradient-to-r from-[#6DBF7C] to-[#43A047]' : hpPct > 25 ? 'bg-gradient-to-r from-[#FDD835] to-[#F9A825]' : 'bg-gradient-to-r from-[#D4534A] to-[#C62828]'"
        ></div>
      </div>
    </div>

    <div v-if="showEnergy && sprite" class="flex items-center gap-1">
      <span class="text-xs text-[#6B5E4F]">能量</span>
      <div class="flex gap-0.5">
        <div
          v-for="i in 10"
          :key="i"
          class="w-2.5 h-4 rounded-sm transition-colors duration-300"
          :class="i <= (sprite.energy || 0) ? 'bg-gradient-to-b from-[#C9A96E] to-[#A08050]' : 'bg-[#D4C8B8]'"
        ></div>
      </div>
      <span class="text-xs text-[#6B5E4F] ml-1">{{ sprite.energy || 0 }}/10</span>
    </div>
  </div>
</template>
