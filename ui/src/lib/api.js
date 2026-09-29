async function request(path, options = {}) {
  const res = await fetch(path, {
    headers: { 'Content-Type': 'application/json' },
    ...options,
    body: options.body ? JSON.stringify(options.body) : undefined,
  })
  if (!res.ok) {
    const problem = await res.json().catch(() => ({
      error: 'HTTPError', message: `${res.status} ${res.statusText}`, hint: '',
    }))
    throw Object.assign(new Error(problem.message), problem)
  }
  return res.status === 204 ? null : res.json()
}

export const getLibrary = () => request('/api/library')
export const importSource = (body) => request('/api/library/import', { method: 'POST', body })
export const getProject = (id) => request(`/api/project/${encodeURIComponent(id)}`)
export const putPage = (id, pageId, body) =>
  request(`/api/project/${encodeURIComponent(id)}/page/${encodeURIComponent(pageId)}`,
          { method: 'PUT', body })
export const postDetect = (id, keepEdits = false) =>
  request(`/api/project/${encodeURIComponent(id)}/detect`, { method: 'POST', body: { keep_edits: keepEdits } })
export const postAssemble = (id) =>
  request(`/api/project/${encodeURIComponent(id)}/assemble`, { method: 'POST' })
export const getPreview = (id) => request(`/api/project/${encodeURIComponent(id)}/preview`)
export const getJobs = () => request('/api/jobs')
export const cancelJob = (jobId) => request(`/api/jobs/${jobId}/cancel`, { method: 'POST' })

export function subscribeJobs(onJob) {
  const source = new EventSource('/api/events')
  source.onmessage = (event) => onJob(JSON.parse(event.data))
  return () => source.close()
}
