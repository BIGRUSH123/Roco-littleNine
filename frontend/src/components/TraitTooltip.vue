<script setup>
import { computed } from 'vue'

const props = defineProps({
  trait: { type: [Object, String], default: null },
  effects: { type: Array, default: () => [] },
})

const traitName = computed(() => {
  if (!props.trait) return ''
  return typeof props.trait === 'string' ? props.trait : props.trait.name
})

const traitDesc = computed(() => {
  if (!props.trait || typeof props.trait === 'string') return ''
  return props.trait.description || ''
})

const traitEffects = computed(() => {
  if (!traitName.value) return []
  // New API: display_effects is on the trait object itself
  if (props.trait && typeof props.trait !== 'string' && props.trait.display_effects) {
    return props.trait.display_effects
  }
  // Fallback: search effects array by source (old API)
  const all = props.effects || []
  return all.filter(
    e => e.source === traitName.value && (e.display_mult != null || e.display_value != null)
  )
})

function formatMult(val) {
  const pct = Math.round(val * 100)
  const sign = pct >= 0 ? '+' : ''
  return `${sign}${pct}%`
}

function formatEffect(eff) {
  if (eff.display_value != null) {
    const sign = eff.display_value >= 0 ? '+' : ''
    return `${sign}${Math.round(eff.display_value)}`
  }
  if (eff.display_mult != null) {
    return formatMult(eff.display_mult)
  }
  return ''
}
</script>

<template>
  <div v-if="traitName" class="group relative inline-flex">
    <span
      class="px-2 py-0.5 text-[11px] rounded-full border font-medium transition-colors
             bg-[#FFF8E1] border-[#C9A96E]/40 text-[#8D6E3F] hover:bg-[#FFECB3]
             cursor-default"
    >
      {{ traitName }}
    </span>

    <div
      class="absolute bottom-full left-1/2 -translate-x-1/2 mb-1 z-[100]
             opacity-0 group-hover:opacity-100 transition-opacity duration-150
             pointer-events-none whitespace-nowrap"
    >
      <div class="bg-[#3D2B1F] text-[#FBF7F0] text-xs px-3 py-2 rounded-lg shadow-lg">
        <div class="font-bold text-sm mb-1 text-[#C9A96E]">
          特性: {{ traitName }}
        </div>
        <div v-if="traitDesc" class="text-[#D4C8B8] leading-relaxed mb-1">
          {{ traitDesc }}
        </div>
        <div
          v-for="(eff, i) in traitEffects"
          :key="i"
          class="text-[#6DBF7C] font-medium"
        >
          {{ eff.name }}{{ formatEffect(eff) }}
        </div>
        <div
          v-if="!traitDesc && traitEffects.length === 0"
          class="text-[#D4C8B8] italic"
        >
          暂无效果数据
        </div>
      </div>
    </div>
  </div>
</template>
