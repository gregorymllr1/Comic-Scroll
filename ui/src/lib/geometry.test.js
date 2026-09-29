import { describe, it, expect } from 'vitest'
import { fitScale, toCanvas, toImage, hitTest, clampBox } from './geometry.js'

const view = { scale: 0.5, offsetX: 10, offsetY: 20 }

describe('fitScale', () => {
  it('fits by the tighter axis', () => {
    expect(fitScale(1000, 2000, 500, 500)).toBe(0.25)
    expect(fitScale(2000, 1000, 500, 500)).toBe(0.25)
  })
  it('never upscales past 1', () => {
    expect(fitScale(100, 100, 500, 500)).toBe(1)
  })
})

describe('round trip', () => {
  it('toImage undoes toCanvas', () => {
    const box = [100, 200, 300, 400]
    const rect = toCanvas(box, view)
    expect(toImage(rect, view)).toEqual(box)
  })
  it('places the box using scale and offset', () => {
    expect(toCanvas([100, 200, 300, 400], view)).toEqual({ x: 60, y: 120, w: 150, h: 200 })
  })
})

describe('hitTest', () => {
  const boxes = [[0, 0, 200, 200], [400, 400, 200, 200]]
  it('finds a corner handle before the body', () => {
    const corner = toCanvas([0, 0, 200, 200], view)
    expect(hitTest({ x: corner.x + corner.w, y: corner.y + corner.h }, boxes, view))
      .toEqual({ index: 0, handle: 'se' })
  })
  it('finds the body', () => {
    expect(hitTest({ x: 60, y: 70 }, boxes, view)).toEqual({ index: 0, handle: 'move' })
  })
  it('returns null on empty space', () => {
    expect(hitTest({ x: 5, y: 5 }, boxes, view)).toBeNull()
  })
  it('prefers the topmost box when they overlap', () => {
    const stacked = [[0, 0, 400, 400], [0, 0, 200, 200]]
    expect(hitTest({ x: 60, y: 70 }, stacked, view).index).toBe(1)
  })
})

describe('clampBox', () => {
  it('keeps the box inside the image', () => {
    expect(clampBox([-10, -10, 50, 50], 100, 100)).toEqual([0, 0, 40, 40])
    expect(clampBox([80, 80, 50, 50], 100, 100)).toEqual([80, 80, 20, 20])
  })
  it('enforces a minimum size', () => {
    expect(clampBox([10, 10, 1, 1], 100, 100)).toEqual([10, 10, 8, 8])
  })
})
