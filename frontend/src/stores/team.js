import { defineStore } from 'pinia'
import { ref, computed } from 'vue'

export const useTeamStore = defineStore('team', () => {
  const slots = ref(Array(6).fill(null))
  const selectedBloodline = ref(null)
  const selectedItem = ref(null)
  const leadIndex = ref(0)
  const savedTeams = ref([])

  const filledCount = computed(() => slots.value.filter(s => s !== null).length)
  const isReady = computed(() => {
    return slots.value.some((s, i) =>
      s !== null && s.selectedSkills && s.selectedSkills.length > 0
    )
  })

  function setSlot(index, sprite) {
    slots.value[index] = sprite
  }

  function clearSlot(index) {
    slots.value[index] = null
    if (leadIndex.value === index) {
      leadIndex.value = slots.value.findIndex(s => s !== null)
    }
  }

  function buildTeamPayload() {
    const team = []
    const slotToTeam = {}
    slots.value.forEach((s, i) => {
      if (s) {
        slotToTeam[i] = team.length
        team.push({
          name: s.name,
          skills: s.selectedSkills || s.skills || [],
          bloodline: s.bloodline || undefined,
        })
      }
    })
    return { team, leadIndex: slotToTeam[leadIndex.value] ?? 0 }
  }

  return {
    slots, selectedBloodline, selectedItem, leadIndex, savedTeams,
    filledCount, isReady,
    setSlot, clearSlot, buildTeamPayload,
  }
})
