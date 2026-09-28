const TOKEN_KEY = 'fm_token'

export function getToken(): string | null {
  return localStorage.getItem(TOKEN_KEY)
}

export function setToken(token: string | null) {
  if (token) localStorage.setItem(TOKEN_KEY, token)
  else localStorage.removeItem(TOKEN_KEY)
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers)
  if (!headers.has('Content-Type') && init.body) {
    headers.set('Content-Type', 'application/json')
  }
  const token = getToken()
  if (token) headers.set('Authorization', `Bearer ${token}`)

  const res = await fetch(path, { ...init, headers, credentials: 'include' })
  if (res.status === 204) return undefined as T
  const data = await res.json().catch(() => ({}))
  if (!res.ok) {
    const detail = data.detail
    const msg = typeof detail === 'string' ? detail : JSON.stringify(detail || data)
    throw new Error(msg || res.statusText)
  }
  return data as T
}

export type User = {
  id: number
  username: string
  is_admin: boolean
  is_active: boolean
  feishu_webhook: string
  created_at: string
}

export type Task = {
  id: number
  origin: string
  dest: string
  origin_codes: string
  dest_codes: string
  start_date: string
  end_date: string
  stay_min: number
  stay_max: number
  adults: number
  currency: string
  cabin: string
  top_n: number
  target_price: number | null
  interval_hours: number
  enabled: boolean
  best_price_seen: number | null
  last_run_at: string | null
  next_run_at: string | null
  created_at: string
  estimated_combinations: number
}

export type PlaceSuggestion = {
  kind: string
  id: string
  label: string
  subtitle: string
  display: string
  codes: string[]
}

export type FlightResult = {
  rank: number
  outbound_date: string
  return_date: string
  trip_days: number
  total_price: number
  cache_price: number
  verified_price: number | null
  verify_status: string
  currency: string
  outbound_summary: string
  return_summary: string
  booking_class: string
  source: string
  verify_url: string
  verify_url_ctrip: string
  verify_url_qunar: string
}

export type Health = {
  ok: boolean
  provider: string
  pipeline?: string
  demo: boolean
  public_mode?: boolean
  travelpayouts_configured: boolean
  travelpayouts_ok?: boolean
  playwright_ok?: boolean
  hint: string
}

export type ScanRun = {
  id: number
  task_id: number
  trigger: string
  status: string
  phase: string
  progress_done: number
  progress_total: number
  progress_message: string
  min_price: number | null
  error: string
  notify_message: string
  started_at: string | null
  finished_at: string | null
  results: FlightResult[]
}

export const api = {
  health: () => request<Health>('/api/health'),
  login: (username: string, password: string) =>
    request<{ access_token: string }>('/api/auth/login', {
      method: 'POST',
      body: JSON.stringify({ username, password }),
    }),
  me: () => request<User>('/api/me'),
  updateMe: (body: { feishu_webhook?: string; password?: string }) =>
    request<User>('/api/me', { method: 'PATCH', body: JSON.stringify(body) }),
  suggestPlaces: (q: string, limit = 20) =>
    request<PlaceSuggestion[]>(`/api/places/suggest?q=${encodeURIComponent(q)}&limit=${limit}`),
  listTasks: () => request<Task[]>('/api/tasks'),
  getTask: (id: number) => request<Task>(`/api/tasks/${id}`),
  createTask: (body: Record<string, unknown>) =>
    request<Task>('/api/tasks', { method: 'POST', body: JSON.stringify(body) }),
  updateTask: (id: number, body: Record<string, unknown>) =>
    request<Task>(`/api/tasks/${id}`, { method: 'PATCH', body: JSON.stringify(body) }),
  deleteTask: (id: number) => request<void>(`/api/tasks/${id}`, { method: 'DELETE' }),
  refreshTask: (id: number) =>
    request<{ run_id: number; status: string }>(`/api/tasks/${id}/refresh`, { method: 'POST' }),
  cancelRun: (taskId: number, runId: number) =>
    request<ScanRun>(`/api/tasks/${taskId}/runs/${runId}/cancel`, { method: 'POST' }),
  latestRun: (id: number) => request<ScanRun | null>(`/api/tasks/${id}/runs/latest`),
  getRun: (taskId: number, runId: number) =>
    request<ScanRun>(`/api/tasks/${taskId}/runs/${runId}`),
  listUsers: () => request<User[]>('/api/admin/users'),
  createUser: (body: { username: string; password: string; is_admin?: boolean }) =>
    request<User>('/api/admin/users', { method: 'POST', body: JSON.stringify(body) }),
  deactivateUser: (id: number) =>
    request<void>(`/api/admin/users/${id}`, { method: 'DELETE' }),
}
