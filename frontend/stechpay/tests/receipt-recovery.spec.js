import { test, expect } from '@playwright/test'

const reference = 'STECH-TEST-BROWSER'
const receiptURL = `/api/payments/${reference}/receipt/`

test.beforeEach(async ({ page }) => {
  let verified = false
  await page.route('**/api/**', route => {
    const path = new URL(route.request().url()).pathname
    if (path === receiptURL) return route.fulfill({ contentType: 'application/pdf', body: '%PDF-1.4\nBrowser test PDF\n%%EOF' })
    if (path === '/api/receipts/recovery/confirm/') verified = true
    const body = path === '/api/meta/' ? { session: '2026/2027', session_start: 2026, payment_mode: 'test', departments: [], fee_map: {} }
      : path === '/api/admin/me/' ? { authenticated: false }
      : path.includes('/receipts/recovery') ? verified ? { receipt_url: receiptURL, receipt_id: 'original-receipt-number' }
        : { detail: 'If this reference has an eligible receipt, a code will be emailed.', expires_at: new Date(Date.now() + 600000).toISOString(), resend_at: new Date(Date.now() + 60000).toISOString(), resends_remaining: 3 }
      : {}
    return route.fulfill({ json: body, status: path === '/api/receipts/recovery/' ? 202 : 200 })
  })
})

test('public navigation shows recovery instead of administrator access', async ({ page }) => {
  await page.goto('/')
  await expect(page.getByRole('button', { name: 'Admin Dashboard', exact: true })).toHaveCount(0)
  await page.getByRole('button', { name: 'Retrieve Receipt', exact: true }).click()
  await expect(page).toHaveURL(/\/receipts$/)
  await expect(page.getByLabel('Payment reference')).toBeVisible()
  await page.goto('/sign-in')
  await expect(page.getByLabel('Password')).toBeVisible()
})

test('email link prefills reference and verified access downloads the original PDF after refresh', async ({ page }) => {
  await page.goto(`/receipts?ref=${reference}`)
  await expect(page.getByLabel('Payment reference')).toHaveValue(reference)
  await page.getByRole('button', { name: 'Send verification code' }).click()
  await expect(page).toHaveURL(/\/receipts\/verify$/)
  await expect(page.getByRole('button', { name: /Resend code in/ })).toBeDisabled()
  await page.getByLabel('Verification code').fill('123456')
  await page.getByRole('button', { name: 'Verify code', exact: true }).click()
  await expect(page.getByRole('heading', { name: 'Your receipt is ready' })).toBeVisible()
  await page.reload()
  await expect(page.getByRole('heading', { name: 'Your receipt is ready' })).toBeVisible()
  const download = page.waitForEvent('download')
  await page.getByRole('button', { name: 'Download PDF receipt' }).click()
  expect((await download).suggestedFilename()).toBe('STechPay-receipt.pdf')
  await expect(page.getByText(/Open it from your downloads/)).toBeVisible()
})

test('incorrect and expired codes keep the student in recovery', async ({ page }) => {
  await page.route('**/api/receipts/recovery/confirm/', route => route.fulfill({ status: 400, json: { detail: 'The code could not be verified.' } }))
  await page.goto('/receipts/verify')
  await page.getByLabel('Verification code').fill('111111')
  await page.getByRole('button', { name: 'Verify code', exact: true }).click()
  await expect(page.getByRole('alert')).toContainText('could not be verified')
  await expect(page.getByRole('button', { name: 'Download PDF receipt' })).toHaveCount(0)
  await page.route('**/api/receipts/recovery/challenge/', route => route.fulfill({ status: 400, json: { code: 'challenge_expired', detail: 'Request a new code.' } }))
  await page.reload()
  await expect(page.getByRole('button', { name: 'Verify code', exact: true })).toBeDisabled()
  await page.getByRole('button', { name: 'Use another reference' }).click()
  await expect(page.getByLabel('Payment reference')).toBeVisible()
})

test('network failure retains the reference and download failure offers retry', async ({ page }) => {
  await page.route('**/api/receipts/recovery/', route => route.abort())
  await page.goto('/receipts')
  await page.getByLabel('Payment reference').fill(reference)
  await page.getByRole('button', { name: 'Send verification code' }).click()
  await expect(page.getByRole('alert')).toContainText('Check your connection')
  await expect(page.getByLabel('Payment reference')).toHaveValue(reference)
  await page.goto('/receipts/verify')
  await page.getByLabel('Verification code').fill('123456')
  await page.getByRole('button', { name: 'Verify code', exact: true }).click()
  await page.route(`**${receiptURL}`, route => route.fulfill({ status: 503, body: 'Private error' }))
  await page.getByRole('button', { name: 'Download PDF receipt' }).click()
  await expect(page.getByRole('alert')).toContainText('try downloading again')
  await expect(page.getByRole('button', { name: 'Download PDF receipt' })).toBeEnabled()
})

test('resend clears the old code and mobile layout fits', async ({ page }) => {
  await page.setViewportSize({ width: 375, height: 812 })
  await page.route('**/api/receipts/recovery/challenge/', route => route.fulfill({ json: {
    expires_at: new Date(Date.now() + 500000).toISOString(), resend_at: new Date(Date.now() - 1000).toISOString(), resends_remaining: 2,
  } }))
  await page.goto('/receipts/verify')
  await page.getByLabel('Verification code').fill('111111')
  await page.getByRole('button', { name: 'Resend code', exact: true }).click()
  await expect(page.getByLabel('Verification code')).toHaveValue('')
  await expect(page.getByRole('status')).toContainText('Use the latest code')
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true)
  await page.screenshot({ path: 'test-results/receipt-recovery-mobile.png', fullPage: true })
})
