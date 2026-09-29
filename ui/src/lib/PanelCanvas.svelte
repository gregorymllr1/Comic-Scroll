<script>
  import { createEventDispatcher, onMount } from 'svelte'
  import { fitScale, toCanvas, toImage, hitTest, clampBox } from './geometry.js'

  export let src, imageW, imageH, panels = [], selectedIndex = -1

  const dispatch = createEventDispatcher()
  let canvas, image, box, drag = null
  let view = { scale: 1, offsetX: 0, offsetY: 0 }

  const ROLE_COLOR = { normal: '#3cdc5a', reaction: '#ffb450', splash: '#ff5050' }

  onMount(() => {
    image = new Image()
    image.onload = () => { resize(); draw() }
    image.src = src
  })
  $: if (image && src) { image.src = src }

  function resize() {
    if (!canvas || !box) return
    canvas.width = box.clientWidth
    canvas.height = box.clientHeight
    const scale = fitScale(imageW, imageH, canvas.width, canvas.height)
    view = {
      scale,
      offsetX: (canvas.width - imageW * scale) / 2,
      offsetY: (canvas.height - imageH * scale) / 2,
    }
  }

  function draw() {
    if (!canvas || !image) return
    const ctx = canvas.getContext('2d')
    ctx.clearRect(0, 0, canvas.width, canvas.height)
    const r = toCanvas([0, 0, imageW, imageH], view)
    ctx.drawImage(image, r.x, r.y, r.w, r.h)
    panels.forEach((panel, i) => {
      const box2 = toCanvas(panel.bbox, view)
      ctx.lineWidth = i === selectedIndex ? 4 : 2
      ctx.strokeStyle = ROLE_COLOR[panel.role] ?? ROLE_COLOR.normal
      ctx.strokeRect(box2.x, box2.y, box2.w, box2.h)
      // Reading order is the failure a reader notices first, so always show it.
      ctx.fillStyle = ctx.strokeStyle
      ctx.font = 'bold 18px sans-serif'
      ctx.fillText(String(i + 1), box2.x + 6, box2.y + 22)
      if (i === selectedIndex) {
        for (const [hx, hy] of [[box2.x, box2.y], [box2.x + box2.w, box2.y],
                                [box2.x, box2.y + box2.h], [box2.x + box2.w, box2.y + box2.h]]) {
          ctx.fillStyle = '#fff'
          ctx.fillRect(hx - 5, hy - 5, 10, 10)
        }
      }
    })
  }
  $: panels, selectedIndex, draw()

  function pointerPos(event) {
    const rect = canvas.getBoundingClientRect()
    return { x: event.clientX - rect.left, y: event.clientY - rect.top }
  }

  function down(event) {
    const point = pointerPos(event)
    const hit = hitTest(point, panels.map((p) => p.bbox), view)
    if (hit) {
      dispatch('select', hit.index)
      drag = { ...hit, start: point, original: [...panels[hit.index].bbox] }
    } else {
      drag = { index: -1, handle: 'new', start: point, original: null }
    }
  }

  function move(event) {
    if (!drag) return
    const point = pointerPos(event)
    if (drag.handle === 'new') { draw(); return }
    const dx = (point.x - drag.start.x) / view.scale
    const dy = (point.y - drag.start.y) / view.scale
    let [x, y, w, h] = drag.original
    if (drag.handle === 'move') { x += dx; y += dy }
    if (drag.handle === 'se') { w += dx; h += dy }
    if (drag.handle === 'nw') { x += dx; y += dy; w -= dx; h -= dy }
    if (drag.handle === 'ne') { y += dy; w += dx; h -= dy }
    if (drag.handle === 'sw') { x += dx; w -= dx; h += dy }
    const next = [...panels]
    next[drag.index] = { ...next[drag.index], bbox: clampBox([x, y, w, h], imageW, imageH) }
    panels = next
  }

  function up(event) {
    if (!drag) return
    if (drag.handle === 'new') {
      const point = pointerPos(event)
      const rect = {
        x: Math.min(drag.start.x, point.x), y: Math.min(drag.start.y, point.y),
        w: Math.abs(point.x - drag.start.x), h: Math.abs(point.y - drag.start.y),
      }
      if (rect.w > 12 && rect.h > 12) {
        const bbox = clampBox(toImage(rect, view), imageW, imageH)
        panels = [...panels, {
          id: `new-${Date.now()}`, bbox, order: panels.length, role: 'normal',
          scale: 1.0, gutter_after: 'medium', locked: false, score: 1.0,
        }]
        dispatch('select', panels.length - 1)
      }
    }
    drag = null
    dispatch('change', panels)
  }
</script>

<svelte:window on:resize={() => { resize(); draw() }} />
<div bind:this={box} class="canvas-box">
  <canvas bind:this={canvas} on:pointerdown={down} on:pointermove={move} on:pointerup={up}></canvas>
</div>

<style>
  .canvas-box { width: 100%; height: 100%; }
  canvas { display: block; touch-action: none; cursor: crosshair; }
</style>
