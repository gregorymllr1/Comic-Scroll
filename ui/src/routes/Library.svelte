<script>
  import { onMount } from 'svelte'
  import { chapters, activeJobsByProject, refreshLibrary, showError } from '../lib/stores.js'
  export let onOpen, onImport

  onMount(() => { refreshLibrary().catch(showError) })

  const LABEL = {
    processing: 'Processing', new: 'Not detected', needs_review: 'Needs review',
    reviewed: 'Reviewed', ready: 'Ready', damaged: 'Damaged',
  }
</script>

<header>
  <h1>Library</h1>
  <button on:click={onImport}>Import comic</button>
  <button on:click={() => refreshLibrary().catch(showError)}>Rescan</button>
</header>

{#if $chapters.length === 0}
  <p class="empty">
    No comics yet. Import a folder of scans, a CBZ, or a PDF of a comic you own.
  </p>
{:else}
  <ul class="grid">
    {#each $chapters as chapter (chapter.id)}
      {@const job = $activeJobsByProject[chapter.id]}
      <li class:damaged={chapter.status === 'damaged'}>
        <button on:click={() => chapter.status !== 'damaged' && onOpen(chapter.id)}>
          <img src={`/media/${chapter.id}/work/cleaned/${chapter.id}.jpg?w=200`} alt="" />
          <strong>{chapter.name}</strong>
          <span class="badge">{LABEL[chapter.status] ?? chapter.status}</span>
          {#if chapter.page_count}
            <span>{chapter.page_count} pages · {chapter.panel_count} panels</span>
          {/if}
          {#if chapter.needs_review}
            <span>{chapter.needs_review} to check</span>
          {/if}
          {#if job}
            <progress value={job.done} max={job.total || 1}></progress>
            <span>{job.message}</span>
          {/if}
          {#if chapter.error}<span class="error">{chapter.error}</span>{/if}
        </button>
      </li>
    {/each}
  </ul>
{/if}
