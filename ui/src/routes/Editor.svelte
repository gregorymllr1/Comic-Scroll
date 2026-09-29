<script>
  import { onMount } from 'svelte'
  import PanelCanvas from '../lib/PanelCanvas.svelte'
  import { getProject, putPage, postDetect, postAssemble } from '../lib/api.js'
  import { showError } from '../lib/stores.js'

  export let chapterId, onBack, onRead

  let project = null, pageIndex = 0, selectedIndex = -1, saveTimer

  $: page = project?.pages?.[pageIndex] ?? null
  $: selected = page?.panels?.[selectedIndex] ?? null

  onMount(async () => {
    try {
      project = await getProject(chapterId)
      // Open on the first page detection was unsure about, not always page 1.
      const flagged = project.pages.findIndex((p) => p.needs_review)
      pageIndex = flagged >= 0 ? flagged : 0
    } catch (err) { showError(err) }
  })

  function queueSave() {
    clearTimeout(saveTimer)
    const target = page
    saveTimer = setTimeout(() => { saveTimer = null; save(target) }, 600)
  }

  async function save(target) {
    if (!target) return
    try {
      await putPage(chapterId, target.id, {
        panels: target.panels, kind: target.kind, needs_review: false,
      })
      target.needs_review = false
      project = project
    } catch (err) { showError(err) }
  }

  function goToPage(index) {
    if (index !== pageIndex) {
      if (saveTimer) {
        clearTimeout(saveTimer)
        saveTimer = null
        save(page)
      }
      pageIndex = index
    }
    selectedIndex = -1
  }

  function onChange(event) {
    page.panels = event.detail.map((panel, i) => ({ ...panel, order: i }))
    project = project
    queueSave()
  }

  function updateSelected(patch) {
    if (!selected) return
    page.panels[selectedIndex] = { ...selected, ...patch }
    project = project
    queueSave()
  }

  function deleteSelected() {
    if (selectedIndex < 0) return
    page.panels = page.panels.filter((_, i) => i !== selectedIndex).map((p, i) => ({ ...p, order: i }))
    selectedIndex = -1
    project = project
    queueSave()
  }

  const GUTTERS = ['tight', 'medium', 'large']

  function onKey(event) {
    if (event.target.tagName === 'INPUT' || event.target.tagName === 'SELECT') return
    if (event.key === 'Delete') { deleteSelected(); event.preventDefault() }
    if (event.key === 'j') goToPage(Math.min(pageIndex + 1, project.pages.length - 1))
    if (event.key === 'k') goToPage(Math.max(pageIndex - 1, 0))
    if (event.key === 'Tab' && page?.panels?.length) {
      selectedIndex = (selectedIndex + 1) % page.panels.length
      event.preventDefault()
    }
    if (event.key === 'l' && selected) updateSelected({ locked: !selected.locked })
    if ((event.key === '[' || event.key === ']') && selected) {
      const at = GUTTERS.indexOf(selected.gutter_after)
      const next = event.key === '[' ? Math.max(0, at - 1) : Math.min(GUTTERS.length - 1, at + 1)
      updateSelected({ gutter_after: GUTTERS[next] })
    }
    if (['ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown'].includes(event.key) && selected) {
      const step = event.shiftKey ? 10 : 1
      const [x, y, w, h] = selected.bbox
      const moved = {
        ArrowLeft: [x - step, y, w, h], ArrowRight: [x + step, y, w, h],
        ArrowUp: [x, y - step, w, h], ArrowDown: [x, y + step, w, h],
      }[event.key]
      updateSelected({ bbox: moved })
      event.preventDefault()
    }
  }
</script>

<svelte:window on:keydown={onKey} />

{#if project && page}
  <div class="editor">
    <nav class="rail">
      <button on:click={onBack}>← Library</button>
      {#each project.pages as p, i}
        <button class:active={i === pageIndex} on:click={() => goToPage(i)}>
          <img src={`/media/${chapterId}/${p.cleaned}?w=140`} alt="" loading="lazy" />
          <span>{i + 1}</span>
          {#if p.needs_review}<span class="flag" title="Detection was unsure">!</span>{/if}
        </button>
      {/each}
    </nav>

    <main>
      <PanelCanvas
        src={`/media/${chapterId}/${page.cleaned}?w=1200`}
        imageW={page.width} imageH={page.height}
        panels={page.panels} {selectedIndex}
        on:change={onChange} on:select={(e) => (selectedIndex = e.detail)} />
    </main>

    <aside>
      <h2>Page {pageIndex + 1} of {project.pages.length}</h2>
      <label>Kind
        <select bind:value={page.kind} on:change={queueSave}>
          <option value="normal">normal</option><option value="splash">splash</option>
          <option value="spread">spread</option><option value="skip">skip</option>
        </select>
      </label>
      <button on:click={() => postDetect(chapterId, true).catch(showError)}>Re-detect page</button>

      {#if selected}
        <h3>Panel {selectedIndex + 1}</h3>
        <label>Role
          <select value={selected.role} on:change={(e) => updateSelected({ role: e.target.value })}>
            <option value="normal">normal</option><option value="reaction">reaction</option>
            <option value="splash">splash</option>
          </select>
        </label>
        <label>Scale
          <input type="range" min="0.3" max="1" step="0.02" value={selected.scale}
                 on:input={(e) => updateSelected({ scale: Number(e.target.value) })} />
          {selected.scale.toFixed(2)}
        </label>
        <label>Gutter after
          <select value={selected.gutter_after} on:change={(e) => updateSelected({ gutter_after: e.target.value })}>
            {#each GUTTERS as g}<option value={g}>{g}</option>{/each}
          </select>
        </label>
        <label><input type="checkbox" checked={selected.locked}
               on:change={(e) => updateSelected({ locked: e.target.checked })} /> Locked</label>
        <button on:click={deleteSelected}>Delete panel</button>
      {:else}
        <p>Drag on empty space to add a panel.</p>
      {/if}

      <hr />
      <button on:click={onRead}>Preview scroll</button>
      <button on:click={() => postAssemble(chapterId).catch(showError)}>Export CBZ</button>
    </aside>
  </div>
{:else}
  <p>Loading…</p>
{/if}
