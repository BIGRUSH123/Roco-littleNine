<script setup>
import { computed } from 'vue'
import { Popover, PopoverButton, PopoverPanel } from '@headlessui/vue'

const props = defineProps({
  effect: { type: Object, required: true },
})

const isPositive = computed(() => props.effect.category !== 'abnormal')

const icon = computed(() => {
  if (!isPositive.value) {
    const abnormalIcons = {
      '灼烧': '🔥', '中毒': '💀', '冻结': '❄️', '麻痹': '⚡',
      '睡眠': '💤', '混乱': '😵', '寄生': '🌿', '诅咒': '👻',
      '烧伤': '🔥', '冻伤': '❄️',
    }
    return abnormalIcons[props.effect.name] || '⚠️'
  }
  const statIcons = {
    '攻击': '⚔️', '防御': '🛡️', '魔攻': '🔮', '魔防': '🛡️',
    '速度': '💨', '威力': '💥', '命中': '🎯', '闪避': '👟',
  }
  for (const [key, icon] of Object.entries(statIcons)) {
    if (props.effect.name.includes(key)) return icon
  }
  return '⬆️'
})

const sourceName = computed(() => props.effect.source || '')
</script>

<template>
  <Popover class="relative inline-flex">
    <PopoverButton
      :class="[
        'px-2 py-1 text-xs rounded-lg border font-medium transition-colors flex items-center gap-1',
        isPositive
          ? 'bg-[#E8F5E9] border-[#A5D6A7] text-[#5C8D6E] hover:bg-[#C8E6C9]'
          : 'bg-[#EDE7F6] border-[#B39DDB] text-[#4A3B5C] hover:bg-[#D1C4E9]'
      ]"
    >
      <span>{{ icon }}</span>
      <span>{{ effect.name }}</span>
      <span v-if="effect.steps > 0" class="font-mono">+{{ effect.steps }}</span>
      <span v-if="effect.stacks > 1" class="bg-[#3D2B1F] text-white text-[10px] rounded-full w-5 h-5 flex items-center justify-center ml-0.5">
        {{ effect.stacks }}
      </span>
    </PopoverButton>

    <Teleport to="body">
      <PopoverPanel class="absolute z-50">
        <div class="bg-[#3D2B1F] text-[#FBF7F0] text-xs px-3 py-2 rounded-lg shadow-lg whitespace-nowrap">
          <template v-if="isPositive">
            <span class="text-[#6DBF7C]">增益</span>
            <span v-if="effect.name"> · {{ effect.name }}</span>
            <span v-if="effect.steps > 0"> +{{ effect.steps }}级</span>
          </template>
          <template v-else>
            <span class="text-[#D4534A]">异常</span>
            <span v-if="effect.name"> · {{ effect.name }}</span>
            <span v-if="effect.stacks > 1"> ×{{ effect.stacks }}</span>
          </template>
          <div v-if="sourceName" class="text-[#C9A96E] mt-1">
            来源: {{ sourceName }}
          </div>
        </div>
      </PopoverPanel>
    </Teleport>
  </Popover>
</template>
