function getCookie(name) {
  const match = document.cookie.match(new RegExp(`(?:^|; )${name}=([^;]*)`))
  return match ? decodeURIComponent(match[1]) : ''
}

function flattenError(data, fallback) {
  if (!data || typeof data !== 'object') return fallback
  if (typeof data.detail === 'string') return data.detail
  for (const value of Object.values(data)) {
    if (typeof value === 'string') return value
    if (Array.isArray(value) && value.length) return String(value[0])
  }
  return fallback
}

async function ensureCsrf() {
  if (!getCookie('csrftoken')) {
    await fetch('/api/csrf/', { credentials: 'same-origin' })
  }
}

export async function api(path, { method = 'GET', body } = {}) {
  await ensureCsrf()
  const response = await fetch(`/api${path}`, {
    method,
    credentials: 'same-origin',
    headers: {
      ...(body ? { 'Content-Type': 'application/json' } : {}),
      'X-CSRFToken': getCookie('csrftoken') || '',
    },
    body: body ? JSON.stringify(body) : undefined,
  })

  let data = {}
  try {
    data = await response.json()
  } catch {
    data = {}
  }
  if (!response.ok) {
    const error = new Error(flattenError(data, response.statusText || 'Request failed'))
    error.status = response.status
    error.data = data
    throw error
  }
  return data
}

export function naira(value) {
  const num = Number(value)
  return `NGN ${(Number.isFinite(num) ? num : 0).toLocaleString('en-NG', {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  })}`
}
