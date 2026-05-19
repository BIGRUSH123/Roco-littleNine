import { ref } from 'vue'
import gsap from 'gsap'

export function useSpriteAnim(spriteRef, shadowRef) {
  const isAnimating = ref(false)

  async function playIdle() {
    if (!spriteRef.value) return
    gsap.killTweensOf(spriteRef.value, 'scale')
    gsap.to(spriteRef.value, {
      scale: 1.02,
      duration: 1,
      yoyo: true,
      repeat: -1,
      ease: 'sine.inOut',
    })
    if (shadowRef?.value) {
      gsap.to(shadowRef.value, {
        scale: 0.95,
        duration: 1,
        yoyo: true,
        repeat: -1,
        ease: 'sine.inOut',
      })
    }
  }

  function stopIdle() {
    if (spriteRef.value) {
      gsap.killTweensOf(spriteRef.value, 'scale')
      gsap.set(spriteRef.value, { scale: 1 })
    }
    if (shadowRef?.value) {
      gsap.killTweensOf(shadowRef.value, 'scale')
      gsap.set(shadowRef.value, { scale: 1 })
    }
  }

  async function playHit() {
    if (!spriteRef.value) return
    isAnimating.value = true
    const tl = gsap.timeline()
    tl.to(spriteRef.value, {
      x: -5,
      duration: 0.05,
      repeat: 5,
      yoyo: true,
      ease: 'rough({ strength: 3, points: 10 })',
    })
    tl.to(spriteRef.value, {
      filter: 'brightness(1.5) hue-rotate(-30deg)',
      duration: 0.1,
      yoyo: true,
      repeat: 1,
    }, 0)
    await tl.play()
    gsap.set(spriteRef.value, { x: 0, filter: 'none' })
    isAnimating.value = false
  }

  async function playAttack() {
    if (!spriteRef.value) return
    isAnimating.value = true
    const el = spriteRef.value
    const tl = gsap.timeline()
    tl.to(el, {
      x: -20,
      duration: 0.15,
      ease: 'power2.out',
    })
    tl.to(el, {
      boxShadow: '0 0 20px 8px rgba(201,169,110,0.5)',
      duration: 0.1,
    })
    tl.to(el, {
      x: 0,
      boxShadow: '0 0 0px 0px rgba(201,169,110,0)',
      duration: 0.2,
      ease: 'power2.in',
    })
    await tl.play()
    gsap.set(el, { clearProps: 'boxShadow' })
    isAnimating.value = false
  }

  async function playFaint() {
    if (!spriteRef.value) return
    isAnimating.value = true
    await gsap.to(spriteRef.value, {
      filter: 'grayscale(1)',
      opacity: 0.6,
      y: 10,
      duration: 0.5,
      ease: 'power2.out',
    })
    isAnimating.value = false
  }

  async function playEntry() {
    if (!spriteRef.value) return
    isAnimating.value = true
    gsap.set(spriteRef.value, { filter: 'brightness(2)', opacity: 0.3 })
    await gsap.fromTo(spriteRef.value,
      { y: -60, opacity: 0 },
      { y: 0, opacity: 1, duration: 0.4, ease: 'power2.out' }
    )
    gsap.to(spriteRef.value, {
      filter: 'brightness(1)',
      duration: 0.2,
    })
    isAnimating.value = false
    playIdle()
  }

  async function playDamageNumber(el, value, isHeal) {
    if (!el) return
    gsap.set(el, { scale: 0.5, opacity: 1, y: 0 })
    await gsap.to(el, {
      scale: 1.3,
      duration: 0.2,
      ease: 'back.out(2)',
    })
    await gsap.to(el, {
      scale: 0.8,
      opacity: 0,
      y: -40,
      duration: 0.8,
      ease: 'power2.out',
      delay: 0.3,
    })
  }

  async function playHpTransition(barRef, newWidth) {
    if (!barRef.value) return
    await gsap.to(barRef.value, {
      width: newWidth + '%',
      duration: 0.5,
      ease: 'power2.out',
    })
  }

  function cleanup() {
    if (spriteRef.value) {
      gsap.killTweensOf(spriteRef.value)
    }
    isAnimating.value = false
  }

  return {
    isAnimating,
    playIdle, stopIdle,
    playHit, playAttack, playFaint, playEntry,
    playDamageNumber, playHpTransition,
    cleanup,
  }
}
