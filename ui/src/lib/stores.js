import { writable, derived } from 'svelte/store'
import { getLibrary, getJobs, subscribeJobs } from './api.js'

export const chapters = writable([])
export const jobs = writable([])
export const toast = writable(null)

export async function refreshLibrary() {
  chapters.set(await getLibrary())
}

export function showError(err) {
  toast.set({ message: err.message, hint: err.hint || '' })
  setTimeout(() => toast.set(null), 6000)
}

export async function startJobStream() {
  jobs.set(await getJobs())
  return subscribeJobs((job) => {
    jobs.update((list) => {
      const next = list.filter((j) => j.id !== job.id)
      next.push(job)
      return next
    })
    if (['done', 'failed', 'cancelled'].includes(job.state)) refreshLibrary()
    // A job fails on the worker thread, so nothing else would ever tell the user.
    if (job.state === 'failed') {
      toast.set({
        message: job.error || `${job.kind} failed`,
        hint: job.log ? `Details written to ${job.log}` : '',
      })
      setTimeout(() => toast.set(null), 12000)
    }
  })
}

export const activeJobsByProject = derived(jobs, ($jobs) => {
  const map = {}
  for (const job of $jobs) {
    if (job.state === 'queued' || job.state === 'running') map[job.project_id] = job
  }
  return map
})
