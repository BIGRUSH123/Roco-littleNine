import { defineStore } from 'pinia'
import { ref } from 'vue'

export const useSpriteAssetStore = defineStore('spriteAssets', () => {
  const cache = ref(new Map())
  const loading = ref(new Set())
  const errors = ref(new Set())

  function getUrl(name) {
    if (cache.value.has(name)) return cache.value.get(name)
    loadSprite(name)
    return null
  }

  function hasError(name) {
    return errors.value.has(name)
  }

  async function loadSprite(name) {
    if (cache.value.has(name)) return cache.value.get(name)
    if (loading.value.has(name)) return null
    loading.value.add(name)

    return new Promise((resolve) => {
      const img = new Image()
      const url = `/sprites/${encodeURIComponent(name)}.png`
      img.onload = () => {
        cache.value.set(name, url)
        loading.value.delete(name)
        resolve(url)
      }
      img.onerror = () => {
        errors.value.add(name)
        loading.value.delete(name)
        resolve(null)
      }
      img.src = url
    })
  }

  async function preloadTeam(names) {
    const urls = await Promise.all(names.map(n => loadSprite(n)))
    return urls.filter(Boolean)
  }

  return { cache, loading, errors, getUrl, hasError, loadSprite, preloadTeam }
})
