<script setup>
import { computed, ref } from 'vue'
import { Popover, PopoverButton, PopoverPanel } from '@headlessui/vue'
import gsap from 'gsap'

const props = defineProps({
  skill: { type: Object, required: true },
  skillMeta: { type: Object, default: null },
  disabled: { type: Boolean, default: false },
  energyInsufficient: { type: Boolean, default: false },
  selected: { type: Boolean, default: false },
})

const emit = defineEmits(['select'])

const btnRef = ref(null)

const element = computed(() => props.skillMeta?.element || '')
const skillType = computed(() => props.skillMeta?.skill_type || '')
const power = computed(() => props.skill.effective_power || 0)
const energyCost = computed(() => props.skill.effective_energy_cost ?? props.skill.base_energy_cost ?? 0)
const priority = computed(() => props.skillMeta?.priority || 0)
const description = computed(() => props.skillMeta?.description || '')

const elementColorClass = computed(() => element.value ? `element-${element.value}` : '')

function onPointerEnter() {
  if (props.disabled) return
  gsap.to(btnRef.value, { y: -4, boxShadow: '0 0 12px rgba(201,169,110,0.3)', duration: 0.15, ease: 'power2.out' })
}

function onPointerLeave() {
  gsap.to(btnRef.value, { y: 0, boxShadow: '0 2px 0 rgba(61,43,31,0.15)', duration: 0.15, ease: 'power2.out' })
}

function onClick() {
  if (props.disabled) return
  gsap.fromTo(btnRef.value, { scale: 1 }, { scale: 0.9, duration: 0.1, yoyo: true, repeat: 1, ease: 'power2.inOut' })
  emit('select', props.skill.name)
}
</script>

<template>
  <Popover class="relative">
    <PopoverButton as="template" v-slot="{ open }">
      <button
        ref="btnRef"
        @click="onClick"
        @pointerenter="onPointerEnter"
        @pointerleave="onPointerLeave"
        :disabled="disabled"
        :class="[
          'w-full text-left px-3 py-2 rounded-lg border font-medium transition-colors skill-glow btn',
          disabled
            ? 'bg-[#E8E0D5] border-[#D4C8B8] text-[#B0A595] cursor-not-allowed'
            : energyInsufficient
              ? 'bg-[#FFF3E0] border-[#D4534A]/40 text-[#D4534A] hover:border-[#D4534A]'
              : selected
                ? 'bg-[#E8F5E9] border-[#5C8D6E] text-[#5C8D6E] ring-1 ring-[#5C8D6E]/30'
                : 'bg-white border-[#D4C8B8] text-[#3D2B1F] hover:border-[#C9A96E]',
          open ? 'border-[#C9A96E]' : ''
        ]"
      >
        <div class="flex items-center gap-1.5">
          <span v-if="skill.skill_index !== undefined" class="text-xs text-[#6B5E4F] font-mono">[{{ skill.skill_index + 1 }}]</span>
          <span v-if="element" :class="['elem-tag', elementColorClass]">{{ element }}</span>
          <span class="text-sm font-bold truncate">{{ skill.name }}</span>
          <span v-if="priority > 0" class="text-xs text-[#C9A96E] font-bold">☆+{{ priority }}</span>
        </div>
        <div class="flex items-center gap-2 mt-1 text-xs text-[#6B5E4F]">
          <template v-if="power > 0"><span>⚡{{ power }}威</span></template>
          <span v-if="skillType" class="text-[#8D6E63]">{{ { '物攻': '物理', '魔攻': '魔法', '防御': '防御', '辅助': '辅助' }[skillType] || skillType }}</span>
          <span>⚡{{ energyCost }}费</span>
          <span v-if="skill.position_power_bonus > 0" class="text-[#5C8D6E]">+{{ skill.position_power_bonus }}</span>
        </div>
        <div v-if="energyInsufficient && !disabled" class="mt-1 text-xs text-[#D4534A] font-bold">能量不足</div>
      </button>
    </PopoverButton>

    <Teleport to="body">
      <PopoverPanel class="absolute z-50">
        <div class="bg-[#3D2B1F] text-[#FBF7F0] text-xs px-4 py-3 rounded-xl shadow-lg max-w-xs">
          <div class="font-bold text-sm mb-1">{{ skill.name }}</div>
          <div class="text-[#C9A96E] mb-1">
            {{ element }} · {{ { '物攻': '物理攻击', '魔攻': '魔法攻击', '防御': '防御', '辅助': '辅助' }[skillType] || skillType }}
            <template v-if="power > 0"> · {{ power }}威力</template>
            · {{ energyCost }}能量
          </div>
          <div v-if="description" class="text-[#D4C8B8] leading-relaxed">{{ description }}</div>
        </div>
      </PopoverPanel>
    </Teleport>
  </Popover>
</template>
