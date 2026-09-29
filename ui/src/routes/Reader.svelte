<script>
  import { onMount } from 'svelte'
  import { getPreview } from '../lib/api.js'
  import { showError } from '../lib/stores.js'

  export let chapterId, onBack, onEditPage

  let preview = null, showGutters = false, width = 420
  let naturalWidths = {}

  onMount(async () => {
    try { preview = await getPreview(chapterId) } catch (err) { showError(err) }
  })

  // The manifest gives crops in image space; CSS reproduces them without
  // compositing anything server-side.
  function cropStyle(panel, width) {
    if (!panel.bbox) return `width:${width}px`
    const [, , w] = panel.bbox
    const factor = (width * panel.scale) / w
    return `width:${width * panel.scale}px; height:${panel.bbox[3] * factor}px; overflow:hidden; position:relative`
  }

  // Percent width is relative to the crop box, not the source, so the bbox
  // would not fill. After load, size from naturalWidth * factor (px).
  function imageStyle(panel, width, naturalWidth) {
    if (!panel.bbox) return `width:${width}px; display:block`
    const [x, y, w] = panel.bbox
    const factor = (width * panel.scale) / w
    const size = naturalWidth == null ? '' : `width:${naturalWidth * factor}px;`
    return `position:absolute; left:${-x * factor}px; top:${-y * factor}px; ${size}
            transform-origin: top left; image-rendering:auto`
  }

  function onImageLoad(event, i) {
    const nw = event.currentTarget.naturalWidth
    if (naturalWidths[i] !== nw) naturalWidths = { ...naturalWidths, [i]: nw }
  }
</script>

<header>
  <button on:click={onBack}>← Editor</button>
  <label><input type="checkbox" bind:checked={showGutters} /> Show gutters</label>
  <label>Width <input type="range" min="320" max="640" bind:value={width} /> {width}px</label>
</header>

{#if preview}
  <div class="scroll" style={`background: rgb(${preview.background.join(',')})`}>
    {#each preview.panels as panel, i}
      <div class="panel" style={cropStyle(panel, width)} on:click={() => onEditPage(panel.page_id)}>
        <img src={panel.src} style={imageStyle(panel, width, naturalWidths[i])}
             alt="" loading="lazy" on:load={(e) => onImageLoad(e, i)} />
      </div>
      <div class="gutter" style={`height:${panel.gutter_after * (width / preview.canvas_width)}px`}>
        {#if showGutters}<span>{panel.gutter_after}px</span>{/if}
      </div>
    {/each}
  </div>
{:else}
  <p>Composing…</p>
{/if}

<style>
  .scroll { margin: 0 auto; display: flex; flex-direction: column; align-items: center; }
  .panel { cursor: pointer; }
  .gutter { width: 100%; display: grid; place-items: center; }
  .gutter span { font: 11px monospace; color: #888; }
</style>
