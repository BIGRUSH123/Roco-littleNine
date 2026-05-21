<script setup>
import { computed } from 'vue'
import { Popover, PopoverButton, PopoverPanel } from '@headlessui/vue'

const props = defineProps({
  typeChart: { type: Object, required: true },
})

const elements = computed(() => {
  const keys = Object.keys(props.typeChart)
  return keys.length > 0 ? keys : ['光','冰','地','幻','幽','恶','普通','机械','武','毒','水','火','电','翼','草','萌','虫','龙']
})

function effectivenessColor(v) {
  if (v >= 2) return 'bg-[#43A047]/80'
  if (v <= 0.5) return 'bg-[#5C6BC0]/60'
  return 'bg-[#D4C8B8]/40'
}

function effectivenessText(v) {
  if (v === undefined || v === null) return '-'
  return '×' + v
}
</script>

<template>
  <div class="p-3">
    <div class="text-xs font-bold text-[#3D2B1F] mb-2 font-[family-name:var(--font-title)]">属性克制</div>

    <div class="overflow-x-auto">
      <div class="inline-grid gap-px bg-[#D4C8B8] rounded-lg overflow-hidden"
           :style="{ gridTemplateColumns: `auto repeat(${elements.length}, 1fr)` }">
        <div class="bg-[#FBF7F0] p-0.5"></div>
        <div
          v-for="el in elements"
          :key="'col-' + el"
          class="bg-[#FBF7F0] p-0.5 text-center"
        >
          <span :class="['elem-tag text-[9px]', `element-${el}`]">{{ el }}</span>
        </div>

        <template v-for="atkEl in elements" :key="'row-' + atkEl">
          <div class="bg-[#FBF7F0] p-0.5 flex items-center justify-end pr-1">
            <span :class="['elem-tag text-[9px]', `element-${atkEl}`]">{{ atkEl }}</span>
          </div>
          <Popover
            v-for="defEl in elements"
            :key="`${atkEl}-${defEl}`"
            class="relative"
          >
            <PopoverButton class="block w-5 h-5 transition-transform hover:scale-125">
              <div
                :class="['w-full h-full rounded-sm', effectivenessColor((typeChart[atkEl] || {})[defEl] ?? 1.0)]"
              ></div>
            </PopoverButton>

            <Teleport to="body">
              <PopoverPanel class="absolute z-50">
                <div class="bg-[#3D2B1F] text-[#FBF7F0] text-xs px-2 py-1 rounded shadow-lg whitespace-nowrap -translate-x-1/2 -translate-y-full -mt-1">
                  {{ atkEl }} → {{ defEl }}: {{ effectivenessText((typeChart[atkEl] || {})[defEl]) }}
                </div>
              </PopoverPanel>
            </Teleport>
          </Popover>
        </template>
      </div>
    </div>

    <div class="flex gap-3 mt-2 text-[10px] text-[#6B5E4F]">
      <span class="flex items-center gap-1"><span class="w-3 h-3 rounded-sm bg-[#43A047]/80 inline-block"></span>克制</span>
      <span class="flex items-center gap-1"><span class="w-3 h-3 rounded-sm bg-[#5C6BC0]/60 inline-block"></span>抵抗</span>
    </div>
  </div>
</template>
