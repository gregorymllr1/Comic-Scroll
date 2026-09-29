<script>
  import { onMount, onDestroy } from 'svelte'
  import { get } from 'svelte/store'
  import PanelCanvas from '../lib/PanelCanvas.svelte'
  import { getProject, putPage, postDetect, postAssemble } from '../lib/api.js'
  import { jobs, showError } from '../lib/stores.js'

  export let chapterId, onBack, onRead, initialPageId = null

  let project = null, pageIndex = 0, selectedIndex = -1, saveTimer
  let loading = true, loadError = null, alive = true, moving = false
  let saveQueue = Promise.resolve(true)
  let saveFailed = false
  let editGeneration = 0
  let pendingDetectId = null
  let detectStayId = null

  $: page = project?.pages?.[pageIndex] ?? null
  $: selected = page?.panels?.[selectedIndex] ?? null

  function mediaSrc(id, cleaned, width) {
    const path = String(cleaned || '').split('/').map((segment) => encodeURIComponent(segment)).join('/')
    return `/media/${encodeURIComponent(id)}/${path}?w=${width}`
  }

  function openingIndex(pages) {
    if (initialPageId) {
      const chosen = pages.findIndex((p) => p.id === initialPageId)
      if (chosen >= 0) return chosen
    }
    const flagged = pages.findIndex((p) => p.needs_review)
    return flagged >= 0 ? flagged : 0
  }

  // Frozen at send time: the page array is mutated in place by the next edit.
  function snapshot(target) {
    return {
      panels: structuredClone(target.panels ?? []),
      kind: target.kind,
      needs_review: false,
    }
  }

  function startSave(target) {
    if (!target) return saveQueue
    const payload = snapshot(target)
    const pageId = target.id
    const generation = editGeneration
    saveQueue = saveQueue.then(async () => {
      // A re-detect reload superseded this page; don't write the old panels back.
      if (generation !== editGeneration) return true
      try {
        await putPage(chapterId, pageId, payload)
        if (generation !== editGeneration) return true
        target.needs_review = false
        project = project
        saveFailed = false
        return true
      } catch (err) {
        if (generation !== editGeneration) return true
        saveFailed = true
        showError(err)
        return false
      }
    })
    return saveQueue
  }

  function queueSave() {
    clearTimeout(saveTimer)
    const target = page
    saveTimer = setTimeout(() => {
      saveTimer = null
      startSave(target)
    }, 600)
  }

  async function flushSave() {
    const target = page
    if (saveTimer) {
      clearTimeout(saveTimer)
      saveTimer = null
      if (target) startSave(target)
    } else if (saveFailed && target) {
      startSave(target)
    }
    return saveQueue
  }

  async function reloadAt(stayId) {
    editGeneration += 1
    clearTimeout(saveTimer)
    saveTimer = null
    saveFailed = false
    try {
      const fresh = await getProject(chapterId)
      if (!alive) return
      const idx = (fresh.pages || []).findIndex((p) => p.id === stayId)
      project = fresh
      if (idx >= 0) pageIndex = idx
      else if (pageIndex >= (fresh.pages?.length || 0)) pageIndex = 0
      selectedIndex = -1
    } catch (err) {
      if (!alive) return
      showError(err)
      if (page) queueSave()
    }
  }

  function noteDetectJob(job) {
    if (!pendingDetectId || !job || job.id !== pendingDetectId) return
    if (job.state !== 'done' && job.state !== 'failed' && job.state !== 'cancelled') return
    const stay = detectStayId
    const state = job.state
    pendingDetectId = null
    if (state === 'done') reloadAt(stay)
  }

  const unsubscribeJobs = jobs.subscribe((list) => {
    if (!pendingDetectId) return
    noteDetectJob(list.find((item) => item.id === pendingDetectId))
  })

  onMount(async () => {
    try {
      project = await getProject(chapterId)
      pageIndex = openingIndex(project.pages || [])
    } catch (err) {
      loadError = err
      showError(err)
    } finally {
      loading = false
    }
  })

  onDestroy(() => {
    alive = false
    unsubscribeJobs()
  })

  async function goToPage(index) {
    if (!project?.pages?.length) return
    if (index === pageIndex) {
      selectedIndex = -1
      return
    }
    if (index < 0 || index >= project.pages.length || moving) return
    moving = true
    try {
      const ok = await flushSave()
      if (!ok) return
      pageIndex = index
      selectedIndex = -1
    } finally {
      moving = false
    }
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
    if (!project?.pages?.length) return
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

  async function back() {
    if (!(await flushSave())) return
    onBack()
  }

  async function preview() {
    if (!(await flushSave())) return
    onRead()
  }

  async function exportCbz() {
    if (!(await flushSave())) return
    try { await postAssemble(chapterId) } catch (err) { showError(err) }
  }

  async function redetect() {
    if (!(await flushSave())) return
    const stay = page?.id
    if (!stay) return
    try {
      const res = await postDetect(chapterId, false, stay)
      pendingDetectId = res.job_id
      detectStayId = stay
      noteDetectJob(get(jobs).find((item) => item.id === res.job_id))
    } catch (err) { showError(err) }
  }
</script>

<svelte:window on:keydown={onKey} />

{#if project && page}
  <div class="editor">
    <nav class="rail">
      <button on:click={back}>← Library</button>
      {#each project.pages as p, i}
        <button class:active={i === pageIndex} on:click={() => goToPage(i)}>
          <img src={mediaSrc(chapterId, p.cleaned, 140)} alt="" loading="lazy" />
          <span>{i + 1}</span>
          {#if p.needs_review}<span class="flag" title="Detection was unsure">!</span>{/if}
        </button>
      {/each}
    </nav>

    <main>
      <PanelCanvas
        src={mediaSrc(chapterId, page.cleaned, 1200)}
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
      <button on:click={redetect}>Re-detect page</button>

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
      <button on:click={preview}>Preview scroll</button>
      <button on:click={exportCbz}>Export CBZ</button>
    </aside>
  </div>
{:else}
  <div class="unopened">
    <button on:click={back}>← Library</button>
    {#if loadError}
      <p>{loadError.message}</p>
    {:else if project}
      <p>This chapter has no pages.</p>
    {:else if loading}
      <p>Loading…</p>
    {:else}
      <p>Could not open this chapter.</p>
    {/if}
  </div>
{/if}

<style>
  .editor {
    display: grid;
    grid-template-columns: 200px minmax(0, 1fr) 280px;
    grid-template-rows: minmax(0, 1fr);
    height: 100svh;
    min-height: 0;
    overflow: hidden;
  }

  .rail,
  aside {
    overflow: auto;
    min-height: 0;
    background: var(--panel);
    padding: 8px;
  }

  .rail {
    display: flex;
    flex-direction: column;
    gap: 8px;
  }

  .rail button {
    display: block;
    width: 100%;
  }

  .rail img {
    display: block;
    width: 100%;
    height: auto;
  }

  .rail button.active {
    border-color: var(--accent);
  }

  .flag {
    color: #f5c16c;
    font-weight: 700;
  }

  main {
    min-width: 0;
    min-height: 0;
    height: 100%;
    display: flex;
    flex-direction: column;
    background: #111216;
  }

  main :global(.canvas-box) {
    flex: 1 1 auto;
    min-height: 0;
    height: 100%;
  }

  aside label {
    display: block;
    margin: 8px 0;
  }

  aside button,
  aside select,
  aside input {
    margin-top: 4px;
  }

  .unopened {
    padding: 16px;
    display: flex;
    flex-direction: column;
    align-items: flex-start;
    gap: 12px;
  }
</style>
