const HANDLE = 10   // px, corner grab radius in canvas space
const MIN = 8       // px, smallest box in image space

export function fitScale(imageW, imageH, viewW, viewH) {
  return Math.min(1, viewW / imageW, viewH / imageH)
}

export function toCanvas([x, y, w, h], view) {
  return {
    x: x * view.scale + view.offsetX,
    y: y * view.scale + view.offsetY,
    w: w * view.scale,
    h: h * view.scale,
  }
}

export function toImage(rect, view) {
  return [
    Math.round((rect.x - view.offsetX) / view.scale),
    Math.round((rect.y - view.offsetY) / view.scale),
    Math.round(rect.w / view.scale),
    Math.round(rect.h / view.scale),
  ]
}

export function clampBox([x, y, w, h], imageW, imageH) {
  // Far edge of the rounded box. Clipping a negative origin shrinks the
  // span so that edge stays put instead of keeping the original width.
  const right = Math.round(x) + Math.round(w)
  const bottom = Math.round(y) + Math.round(h)
  x = Math.max(0, Math.min(Math.round(x), imageW - MIN))
  y = Math.max(0, Math.min(Math.round(y), imageH - MIN))
  w = Math.max(MIN, Math.min(right - x, imageW - x))
  h = Math.max(MIN, Math.min(bottom - y, imageH - y))
  return [x, y, w, h]
}

export function hitTest(point, boxes, view) {
  // Topmost first, so the box drawn last wins an overlap.
  for (let i = boxes.length - 1; i >= 0; i -= 1) {
    const r = toCanvas(boxes[i], view)
    const corners = {
      nw: [r.x, r.y], ne: [r.x + r.w, r.y],
      sw: [r.x, r.y + r.h], se: [r.x + r.w, r.y + r.h],
    }
    for (const [name, [cx, cy]] of Object.entries(corners)) {
      if (Math.abs(point.x - cx) <= HANDLE && Math.abs(point.y - cy) <= HANDLE) {
        return { index: i, handle: name }
      }
    }
    if (point.x >= r.x && point.x <= r.x + r.w && point.y >= r.y && point.y <= r.y + r.h) {
      return { index: i, handle: 'move' }
    }
  }
  return null
}
