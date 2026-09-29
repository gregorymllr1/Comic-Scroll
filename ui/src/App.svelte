<script>
  import { onMount } from 'svelte'
  import Library from './routes/Library.svelte'
  import Import from './routes/Import.svelte'
  import Editor from './routes/Editor.svelte'
  import Reader from './routes/Reader.svelte'
  import { startJobStream, toast } from './lib/stores.js'

  let view = 'library'
  let activeChapterId = null
  let editorPageId = null
  let unsubscribe

  onMount(async () => { unsubscribe = await startJobStream() })
  $: if (!view) view = 'library'
</script>

{#if view === 'library'}
  <Library onOpen={(id) => { activeChapterId = id; editorPageId = null; view = 'editor' }}
           onImport={() => (view = 'import')} />
{:else if view === 'import'}
  <Import onDone={() => (view = 'library')} />
{:else if view === 'editor'}
  <Editor chapterId={activeChapterId} initialPageId={editorPageId}
          onBack={() => (view = 'library')}
          onRead={() => (view = 'reader')} />
{:else if view === 'reader'}
  <Reader chapterId={activeChapterId} onBack={() => (view = 'editor')}
          onEditPage={(pageId) => { editorPageId = pageId; view = 'editor' }} />
{/if}

{#if $toast}
  <div class="toast"><strong>{$toast.message}</strong>{#if $toast.hint}<span>{$toast.hint}</span>{/if}</div>
{/if}
