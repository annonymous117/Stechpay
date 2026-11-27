import { test, expect } from '@playwright/test'

test('DE and regular cohorts show the correct level and backend-priced request', async ({ page }) => {
  let checkout
  await page.route('**/api/**', route => {
    const path = new URL(route.request().url()).pathname
    if (path === '/api/meta/') return route.fulfill({ json: {
      session: '2026/27', session_start: 2026, payment_mode: 'test',
      departments: [{ code: 'STA', name: 'Statistics' }],
      fee_map: { 'STA/200': '2000', 'STA/300': '3000' },
    } })
    if (path === '/api/payments/initiate/') {
      checkout = route.request().postDataJSON()
      return route.fulfill({ status: 503, json: { detail: 'Test checkout stopped here.' } })
    }
    return route.fulfill({ json: {} })
  })
  await page.goto('/')
  await page.getByLabel('Full name').fill('Example Student')
  await page.getByLabel('Email address').fill('student@example.invalid')
  await page.getByRole('textbox', { name: 'Matric number', exact: true }).fill('STA/24/001')
  await expect(page.getByText('300 Level', { exact: true })).toBeVisible()
  await page.getByRole('textbox', { name: 'Matric number', exact: true }).fill('STA/DE/25/002')
  await expect(page.getByText('300 Level', { exact: true })).toBeVisible()
  await page.getByRole('textbox', { name: 'Matric number', exact: true }).fill('STA/DE/26/003')
  await expect(page.getByText('200 Level', { exact: true })).toBeVisible()
  await page.getByRole('button', { name: /with Paystack/ }).click()
  await expect(page.getByText('STechPay is temporarily unavailable. Please try again shortly.')).toBeVisible()
  expect(checkout).toEqual({ full_name: 'Example Student', email: 'student@example.invalid', matric_number: 'STA/DE/26/003' })
  await page.getByRole('textbox', { name: 'Matric number', exact: true }).fill('202612345678AB')
  await page.getByRole('button', { name: /with Paystack/ }).click()
  await expect(page.getByText('Enter a valid matric number like STA/24/020 or STA/DE/25/020.')).toBeVisible()
})

test('administrators edit live subaccounts without overwriting test destinations', async ({ page }) => {
  let changes
  const department = { id: 1, code: 'STA', name: 'Statistics', subaccount_code: 'ACCT_test', live_subaccount_code: '', is_active: true }
  await page.route('**/api/**', route => {
    const path = new URL(route.request().url()).pathname
    if (path === '/api/meta/') return route.fulfill({ json: { session: '2026/27', session_start: 2026, payment_mode: 'test', departments: [department], fee_map: {} } })
    if (path === '/api/admin/me/') return route.fulfill({ json: { authenticated: true } })
    if (path === '/api/admin/stats/') return route.fulfill({ json: { chart_data: [], by_department: [], by_level: [], total_collected: '0', successful: 0, pending: 0, failed: 0 } })
    if (path === '/api/admin/departments/') return route.fulfill({ json: { departments: [department] } })
    if (path === '/api/admin/departments/1/') {
      changes = route.request().postDataJSON()
      Object.assign(department, changes)
      return route.fulfill({ json: { department } })
    }
    return route.fulfill({ json: {} })
  })
  await page.goto('/dashboard')
  await page.getByRole('button', { name: 'Courses & Subaccounts' }).click()
  await expect(page.getByLabel('STA test subaccount')).toHaveValue('ACCT_test')
  await page.getByLabel('STA live subaccount').fill('ACCT_live')
  await page.getByRole('button', { name: 'Save', exact: true }).click()
  await expect(page.getByText('Updated STA', { exact: true })).toBeVisible()
  expect(changes).toEqual({ live_subaccount_code: 'ACCT_live' })
  await expect(page.getByLabel('STA test subaccount')).toHaveValue('ACCT_test')
  await expect(page.getByLabel('STA live subaccount')).toHaveValue('ACCT_live')
})
