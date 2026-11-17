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

let csrfReady
async function request(url, options) {
  const controller = new AbortController()
  const timer = setTimeout(() => controller.abort(), 25000)
  try { return await fetch(url, { ...options, signal: controller.signal }) }
  catch (cause) {
    throw new Error(cause.name === 'AbortError'
      ? 'The request took too long. Please try again. If you were paying, check the existing payment before starting another.'
      : 'We couldn’t reach STechPay. Check your connection and try again.')
  } finally { clearTimeout(timer) }
}
async function ensureCsrf() {
  csrfReady ||= request('/api/csrf/', { credentials: 'same-origin' }).then((response) => {
    if (!response.ok) throw new Error('Could not start a secure session. Refresh and try again.')
  }).catch((error) => { csrfReady = null; throw error })
  await csrfReady
}

export async function api(path, { method = 'GET', body, headers = {} } = {}) {
  await ensureCsrf()
  const response = await request(`/api${path}`, {
    method,
    credentials: 'same-origin',
    headers: {
      ...(body ? { 'Content-Type': 'application/json' } : {}),
      'X-CSRFToken': getCookie('csrftoken') || '',
      ...headers,
    },
    body: body ? JSON.stringify(body) : undefined,
  })

  let data = {}
  try {
    data = await response.json()
  } catch {
    if (response.ok) throw new Error('STechPay returned an unexpected response. Please try again shortly.')
  }
  if (!response.ok) {
    const fallback = response.status === 403 ? 'Your session may have expired, or you do not have access. Please sign in again.'
      : response.status === 404 ? 'The requested item could not be found.'
      : response.status === 429 ? 'Too many requests. Please wait a moment and try again.' : 'We couldn’t complete the request. Please try again.'
    const error = new Error(response.status >= 500 ? 'STechPay is temporarily unavailable. Please try again shortly.' : flattenError(data, fallback))
    if (data.code === 'csrf_failed') csrfReady = null
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

export async function downloadReceipt(path) {
  if (!/^\/api\/payments\/[A-Za-z0-9_-]+\/receipt\/$/.test(path)) throw new Error('Invalid receipt link.')
  const response = await request(path, { credentials: 'same-origin' })
  if (!response.ok) {
    const message = response.status >= 500 ? 'Receipt verification is temporarily unavailable. Please try downloading again.'
      : 'This receipt is no longer available through this verification. Request a new code or contact the administration.'
    throw new Error(message)
  }
  if (!response.headers.get('Content-Type')?.includes('application/pdf')) throw new Error('We couldn’t prepare the PDF. Please try again.')
  const url = URL.createObjectURL(await response.blob())
  const link = document.createElement('a')
  link.href = url; link.download = 'STechPay-receipt.pdf'
  document.body.appendChild(link); link.click(); link.remove()
  setTimeout(() => URL.revokeObjectURL(url), 30000)
}
