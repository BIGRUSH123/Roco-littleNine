import { defineStore } from 'pinia'
import { ref, computed } from 'vue'

export const useBattleStore = defineStore('battle', () => {
  const selfSprite = ref(null)
  const oppSprite = ref(null)
  const selfTeam = ref([])
  const oppTeam = ref([])
  const turn = ref(0)
  const maxTurn = ref(150)
  const weather = ref(null)
  const weatherTurns = ref(0)
  const selfSkills = ref([])
  const selectedSkill = ref(null)
  const marksA = ref([])
  const marksB = ref([])
  const markEnergyModA = ref(0)
  const markEnergyModB = ref(0)
  const turnSnapshots = ref([])
  const replayMode = ref(false)
  const replayTurn = ref(0)
  const isProcessing = ref(false)
  const battlePhase = ref('selection')
  const winner = ref(null)
  const logEntries = ref([])
  const sessionId = ref(null)
  const isFinished = ref(false)
  const activeIndexA = ref(0)
  const activeIndexB = ref(0)
  const selfItem = ref(null)

  function updateFromResponse(data) {
    if (data.session_id) sessionId.value = data.session_id
    if (data.turn !== undefined) turn.value = data.turn
    if (data.is_finished !== undefined) {
      isFinished.value = data.is_finished
      if (data.is_finished) battlePhase.value = 'result'
    }
    if (data.winner) winner.value = data.winner
    if (data.weather !== undefined) weather.value = data.weather
    if (data.weather_turns !== undefined) weatherTurns.value = data.weather_turns
    if (data.mark_energy_mod_a !== undefined) markEnergyModA.value = data.mark_energy_mod_a
    if (data.mark_energy_mod_b !== undefined) markEnergyModB.value = data.mark_energy_mod_b

    if (data.player_a) {
      const pa = data.player_a
      selfTeam.value = pa.team || []
      activeIndexA.value = pa.active_index ?? 0
      selfItem.value = pa.item || null
      const activeIdx = pa.active_index ?? 0
      selfSprite.value = pa.team?.[activeIdx] || null
      selfSkills.value = selfSprite.value?.skills || []
    }

    if (data.player_b) {
      const pb = data.player_b
      oppTeam.value = pb.team || []
      activeIndexB.value = pb.active_index ?? 0
      const activeIdx = pb.active_index ?? 0
      oppSprite.value = pb.team?.[activeIdx] || null
    }

    if (data.marks_a) marksA.value = data.marks_a
    if (data.marks_b) marksB.value = data.marks_b

    if (data.turn_snapshot) {
      turnSnapshots.value.push(data.turn_snapshot)
    }
  }

  function appendLogs(logs) {
    if (logs && logs.length > 0) {
      logEntries.value.push(...logs)
    }
  }

  function setReplayTurn(t) {
    replayTurn.value = t
    const snap = turnSnapshots.value.find(s => s.turn === t)
    if (snap) {
      selfSprite.value = snap.self_sprite
      oppSprite.value = snap.opp_sprite
    }
  }

  function resetBattle() {
    selfSprite.value = null
    oppSprite.value = null
    selfTeam.value = []
    oppTeam.value = []
    turn.value = 0
    weather.value = null
    weatherTurns.value = 0
    selfSkills.value = []
    selectedSkill.value = null
    marksA.value = []
    marksB.value = []
    markEnergyModA.value = 0
    markEnergyModB.value = 0
    turnSnapshots.value = []
    replayMode.value = false
    replayTurn.value = 0
    isProcessing.value = false
    battlePhase.value = 'selection'
    winner.value = null
    logEntries.value = []
    sessionId.value = null
    isFinished.value = false
    activeIndexA.value = 0
    activeIndexB.value = 0
    selfItem.value = null
  }

  return {
    selfSprite, oppSprite, selfTeam, oppTeam,
    turn, maxTurn, weather, weatherTurns,
    selfSkills, selectedSkill,
    marksA, marksB, markEnergyModA, markEnergyModB,
    turnSnapshots, replayMode, replayTurn,
    isProcessing, battlePhase, winner, logEntries, sessionId, isFinished,
    activeIndexA, activeIndexB, selfItem,
    updateFromResponse, appendLogs, setReplayTurn, resetBattle,
  }
})
