<script>
  import { onMount } from 'svelte'
  import Library from './routes/Library.svelte'
  import Import from './routes/Import.svelte'
  import { startJobStream, toast } from './lib/stores.js'

  let view = 'library'
  let activeChapterId = null
  let unsubscribe

  onMount(async () => { unsubscribe = await startJobStream() })
  $: if (!view) view = 'library'
</script>

{#if view === 'library'}
  <Library onOpen={(id) => { activeChapterId = id; view = 'editor' }}
           onImport={() => (view = 'import')} />
{:else if view === 'import'}
  <Import onDone={() => (view = 'library')} />
{/if}

{#if $toast}
  <div class="toast"><strong>{$toast.message}</strong>{#if $toast.hint}<span>{$toast.hint}</span>{/if}</div>
{/if}
