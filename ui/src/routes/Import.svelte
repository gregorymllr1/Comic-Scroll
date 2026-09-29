<script>
  import { importSource } from '../lib/api.js'
  import { refreshLibrary, showError } from '../lib/stores.js'
  export let onDone

  let queued = []
  let source = '', name = '', engine = 'auto', width = 1080

  async function pick(folder) {
    const res = await fetch('/api/dialog/open', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ folder }),
    }).then((r) => r.json())
    if (res.path) {
      source = res.path
      if (!name) name = res.path.split(/[\\/]/).pop().replace(/\.[^.]+$/, '')
    }
  }

  function add() {
    if (!source) return
    queued = [...queued, { source, name: name || source, engine, width }]
    source = ''; name = ''
  }

  async function start() {
    const items = source ? [...queued, { source, name: name || source, engine, width }] : queued
    for (const item of items) {
      try { await importSource(item) } catch (err) { showError(err) }
    }
    await refreshLibrary()
    onDone()
  }
</script>

<h1>Import</h1>
<button on:click={() => pick(false)}>Choose CBZ or PDF…</button>
<button on:click={() => pick(true)}>Choose folder…</button>
<input bind:value={source} placeholder="Source path" />
<input bind:value={name} placeholder="Chapter name" />
<select bind:value={engine}>
  <option value="auto">Detection: automatic</option>
  <option value="yolo">YOLO only</option>
  <option value="cv">OpenCV only</option>
</select>
<input type="number" bind:value={width} min="480" max="2160" />

<button on:click={add} disabled={!source}>Add another</button>
<button on:click={start} disabled={!source && queued.length === 0}>Start</button>

{#if queued.length}
  <ol>{#each queued as item}<li>{item.name}</li>{/each}</ol>
  <p>These run one after another.</p>
{/if}
