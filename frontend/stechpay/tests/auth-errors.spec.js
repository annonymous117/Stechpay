import { test, expect } from '@playwright/test'

const meta = { session: '2026/2027', session_start: 2026, payment_mode: 'test', departments: [{ code: 'CSC', name: 'Computer Science' }], fee_map: { 'CSC/100': '25000' } }
test.beforeEach(async ({ page }) => {
  await page.route('**/api/**', route => {
    const path = new URL(route.request().url()).pathname
    const body = path === '/api/meta/' ? meta : path === '/api/admin/me/' ? { authenticated: false }
      : path === '/api/admin/login/' ? { mfa_required: true }
      : path === '/api/admin/login/challenge/' ? { expires_at: new Date(Date.now() + 300000).toISOString(), resend_at: new Date(Date.now() + 60000).toISOString(), resends_remaining: 3 } : {}
    return route.fulfill({ json: body })
  })
})

test('credentials lead to a separate OTP page; refresh and back work', async ({ page }) => {
  await page.goto('/sign-in')
  await page.getByLabel('Username').fill('browser-test')
  await page.getByLabel('Password').fill('dummy-browser-test')
  await page.getByRole('button', { name: 'Continue', exact: true }).click()
  await expect(page).toHaveURL(/\/sign-in\/verify$/)
  await expect(page.getByRole('heading', { name: 'Check your email' })).toBeVisible()
  await expect(page.getByLabel('Password')).toHaveCount(0)
  await page.getByLabel('Verification code').fill('123456')
  await expect(page.getByRole('button', { name: 'Verify and sign in' })).toBeEnabled()
  await expect(page.getByRole('button', { name: /Resend code in/ })).toBeDisabled()
  await page.reload()
  await expect(page.getByRole('heading', { name: 'Check your email' })).toBeVisible()
  await expect(page.getByLabel('Verification code')).toHaveValue('')
  await page.goBack()
  await expect(page.getByLabel('Password')).toBeVisible()
  await expect(page.getByLabel('Password')).toHaveValue('')
})

test('expired challenge provides a way back and blocks verification', async ({ page }) => {
  await page.route('**/api/admin/login/challenge/', route => route.fulfill({ status: 400, json: { code: 'challenge_expired', detail: 'Start sign-in again.' } }))
  await page.goto('/sign-in/verify')
  await expect(page.getByText('This code has expired. Start sign-in again.')).toBeVisible()
  await expect(page.getByRole('button', { name: 'Verify and sign in' })).toBeDisabled()
  await page.getByRole('button', { name: 'Back to sign in' }).click()
  await expect(page.getByLabel('Username')).toBeVisible()
})

test('network error preserves credentials and hides browser diagnostics', async ({ page }) => {
  await page.route('**/api/admin/login/', route => route.abort())
  await page.goto('/sign-in')
  await page.getByLabel('Username').fill('browser-test')
  await page.getByLabel('Password').fill('dummy-browser-test')
  await page.getByRole('button', { name: 'Continue', exact: true }).click()
  await expect(page.getByRole('alert')).toContainText('Check your connection')
  await expect(page.getByLabel('Username')).toHaveValue('browser-test')
  await expect(page.getByLabel('Password')).toHaveValue('dummy-browser-test')
})

test('custom missing page and unavailable errors', async ({ page }) => {
  await page.goto('/missing-page')
  await expect(page.getByRole('heading', { name: 'Page not found' })).toBeVisible()
  await page.route('**/api/meta/', route => route.fulfill({ status: 503, body: '<h1>Private server diagnostic</h1>', contentType: 'text/html' }))
  await page.goto('/')
  await expect(page.getByRole('alert')).toContainText('temporarily unavailable')
  await expect(page.getByText('Private server diagnostic')).toHaveCount(0)
})

test('payment retry checks the same reference without starting a checkout', async ({ page }) => {
  const references = []
  let checkouts = 0
  await page.route('**/api/payments/verify/', route => {
    references.push(route.request().postDataJSON().reference)
    return route.fulfill({ status: 503, json: { detail: 'Temporary outage' } })
  })
  await page.route('**/api/payments/initiate/', route => { checkouts += 1; return route.fulfill({ json: {} }) })
  await page.goto('/callback?reference=existing-test-payment')
  await expect(page.getByRole('button', { name: 'Try again' })).toBeVisible()
  await page.getByRole('button', { name: 'Try again' }).click()
  await expect(page.getByRole('button', { name: 'Try again' })).toBeVisible()
  expect(references).toEqual(['existing-test-payment', 'existing-test-payment'])
  expect(checkouts).toBe(0)
  await expect(page.getByLabel('Full name')).toHaveCount(0)
})

test('verification screen fits mobile and accepts a pasted code', async ({ page }) => {
  await page.setViewportSize({ width: 375, height: 812 })
  await page.goto('/sign-in/verify')
  await page.getByLabel('Verification code').fill('123456')
  await expect(page.getByLabel('Verification code')).toHaveValue('123456')
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true)
  await page.screenshot({ path: 'test-results/otp-mobile.png', fullPage: true })
})

test('resend resets the input and successful verification opens the dashboard', async ({ page }) => {
  let resends = 0
  await page.route('**/api/admin/login/challenge/', route => {
    if (route.request().method() === 'POST') resends += 1
    return route.fulfill({ json: { expires_at: new Date(Date.now() + 240000).toISOString(), resend_at: new Date(Date.now() - 1000).toISOString(), resends_remaining: 2 } })
  })
  await page.route('**/api/admin/stats/', route => route.fulfill({ json: { total_collected: 0, successful: 0, pending: 0, failed: 0, by_level: [], by_department: [], chart_data: [] } }))
  await page.goto('/sign-in/verify')
  await page.getByLabel('Verification code').fill('111111')
  await page.getByRole('button', { name: 'Resend code', exact: true }).click()
  await expect(page.getByLabel('Verification code')).toHaveValue('')
  expect(resends).toBe(1)
  await page.getByLabel('Verification code').fill('654321')
  await page.getByRole('button', { name: 'Verify and sign in' }).click()
  await expect(page).toHaveURL(/\/dashboard$/)
  await expect(page.getByRole('button', { name: 'Log out', exact: true })).toBeVisible()
})

test('failed checkout retains form entries and reuses its idempotency key', async ({ page }) => {
  const keys = []
  await page.route('**/api/payments/initiate/', route => {
    keys.push(route.request().headers()['idempotency-key'])
    return route.abort()
  })
  await page.goto('/')
  await page.getByLabel('Full name').fill('Browser Test')
  await page.getByLabel('Email address').fill('test@example.invalid')
  await page.getByRole('textbox', { name: 'Matric number', exact: true }).fill('CSC/26/001')
  await page.getByRole('button', { name: /Pay.*Paystack/ }).click()
  await expect(page.getByText(/Check your connection/)).toBeVisible()
  await expect(page.getByLabel('Full name')).toHaveValue('Browser Test')
  await page.getByRole('button', { name: /Pay.*Paystack/ }).click()
  await expect.poll(() => keys.length).toBe(2)
  expect(keys[0]).toBeTruthy()
  expect(keys[1]).toBe(keys[0])
})
