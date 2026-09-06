import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { api, naira } from './lib/api'
import logo from './assets/logo.png'
import './App.css'

const MATRIC_RE = /^([A-Z]{2,5})\/(\d{2})\/(\d{2,6})$/

function parseMatric(raw, sessionStart) {
  const value = String(raw || '').toUpperCase().replace(/[\s-]+/g, '')
  const match = value.match(MATRIC_RE)
  if (!match || !sessionStart) return null
  const entryYear = 2000 + parseInt(match[2], 10)
  if (entryYear > sessionStart) return null
  // Academic level: 1st year (entryYear == sessionStart) is 100L, 2nd year is 200L, etc.
  const level = Math.min(Math.max((sessionStart - entryYear + 1) * 100, 100), 500)
  return { department: match[1], level, normalized: value }
}

function Badge({ tone = 'blue', children }) {
  return <span className={`badge badge-${tone}`}>{children}</span>
}

function Spinner() {
  return <span className="spinner" aria-label="Loading" />
}

/* ------------------------------ Payment view ----------------------------- */

function PaymentForm({ meta, onSuccess }) {
  const [form, setForm] = useState({ full_name: '', email: '', matric_number: '' })
  const [errors, setErrors] = useState({})
  const [submitting, setSubmitting] = useState(false)
  const [banner, setBanner] = useState(null)

  const parsed = parseMatric(form.matric_number, meta.session_start)
  const knownDept = parsed && meta.departments.some((d) => d.code === parsed.department)
  const feeKey = parsed ? `${parsed.department}/${parsed.level}` : null
  const feeAmount = feeKey ? meta.fee_map[feeKey] : null

  const setField = (name, value) => {
    setForm((f) => ({ ...f, [name]: value }))
    setErrors((e) => ({ ...e, [name]: undefined }))
  }

  async function handleSubmit(event) {
    event.preventDefault()
    setBanner(null)
    if (!parsed || !knownDept) {
      setErrors({
        matric_number: 'Enter a valid matric number like STA/24/020 or CSC/26/001.',
      })
      return
    }
    if (!feeAmount) {
      setErrors({
        matric_number: `No fee configured for ${parsed.department} ${parsed.level} Level yet. Contact the departmental coordinator.`,
      })
      return
    }
    setSubmitting(true)
    try {
      const result = await api('/payments/initiate/', {
        method: 'POST',
        body: {
          full_name: form.full_name.trim(),
          email: form.email.trim(),
          matric_number: form.matric_number.trim(),
        },
      })
      if (result.already_paid) {
        onSuccess(result.payment)
        return
      }
      window.location.href = result.authorization_url
    } catch (error) {
      setErrors(error.data || {})
      setBanner(error.message)
      setSubmitting(false)
    }
  }

  const derivedDeptName =
    parsed && knownDept
      ? meta.departments.find((d) => d.code === parsed.department)?.name
      : null

  return (
    <div className="card payment-card">
      <h2>Departmental Dues</h2>
      <p className="muted">
        Session {meta.session} · Pay your departmental dues securely online.
      </p>

      {meta.paystack_mock_mode && (
        <div className="mock-banner">
          Demo mode — Paystack test environment active.
        </div>
      )}

      <form onSubmit={handleSubmit} noValidate>
        <label className="field">
          <span>Full name</span>
          <input
            type="text"
            value={form.full_name}
            placeholder="e.g. Ada Obi"
            autoComplete="name"
            onChange={(e) => setField('full_name', e.target.value)}
          />
          {errors.full_name && <em className="field-error">{errors.full_name}</em>}
        </label>

        <label className="field">
          <span>Email address</span>
          <input
            type="email"
            value={form.email}
            placeholder="you@student.edu.ng"
            autoComplete="email"
            onChange={(e) => setField('email', e.target.value)}
          />
          {errors.email && <em className="field-error">{errors.email}</em>}
        </label>

        <label className="field">
          <span>Matric number</span>
          <input
            type="text"
            className={`mono ${parsed ? (knownDept ? 'valid' : 'invalid') : ''}`}
            value={form.matric_number}
            placeholder="e.g. ICH/24/020"
            spellCheck="false"
            onChange={(e) => setField('matric_number', e.target.value.toUpperCase())}
          />
          {errors.matric_number && (
            <em className="field-error">{errors.matric_number}</em>
          )}
          {!errors.matric_number && form.matric_number && !parsed && (
            <em className="hint">Format: DEPT/YY/NNN — e.g. CSC/25/014, ICH/24/001</em>
          )}
        </label>

        <label className="field">
          <span>Department / Course</span>
          <select
            value={parsed && knownDept ? parsed.department : ''}
            disabled
            className={parsed && knownDept ? '' : 'placeholder'}
          >
            <option value="">
              {parsed && !knownDept ? `Unknown code “${parsed.department}”` : 'Derived from matric number'}
            </option>
            {knownDept && <option value={parsed.department}>{derivedDeptName}</option>}
          </select>
        </label>

        {parsed && knownDept && feeAmount && (
          <div className="fee-preview">
            <div className="row">
              <span>Department</span>
              <strong>{derivedDeptName}</strong>
            </div>
            <div className="row">
              <span>Derived Level</span>
              <Badge>{parsed.level} Level</Badge>
            </div>
            <div className="row">
              <span>Session</span>
              <strong>{meta.session}</strong>
            </div>
            <div className="row total">
              <span>Amount due</span>
              <strong>{naira(feeAmount)}</strong>
            </div>
          </div>
        )}

        {banner && <div className="alert">{banner}</div>}

        <button className="btn primary block" disabled={submitting} type="submit">
          {submitting ? <Spinner /> : `Pay ${feeAmount ? naira(feeAmount) : ''} with Paystack`}
        </button>
        <p className="fine-print muted">Secured by Paystack · Official PDF Receipt with QR verification</p>
      </form>
    </div>
  )
}

function ResultCard({ payment, onReset }) {
  const success = payment.status === 'successful'
  return (
    <div className={`card result-card ${success ? 'ok' : 'bad'}`}>
      <div className="result-icon">{success ? '✓' : '✕'}</div>
      <h2>{success ? 'Payment Successful' : 'Payment Not Completed'}</h2>
      <p className="muted">
        {success
          ? `${payment.department} dues for ${payment.level} Level (${payment.session}) confirmed.`
          : 'The transaction was not completed. You can safely try again.'}
      </p>

      <dl className="detail-list">
        {[
          ['Receipt No.', success ? payment.receipt_id : null],
          ['Student', payment.full_name],
          ['Matric No.', payment.matric_number],
          ['Department', payment.department],
          ['Level', `${payment.level} Level`],
          ['Session', payment.session],
          ['Amount', naira(payment.amount)],
          ['Reference', payment.reference],
        ]
          .filter(([, v]) => v)
          .map(([k, v]) => (
            <div className="detail-row" key={k}>
              <dt>{k}</dt>
              <dd className={k.includes('No.') || k === 'Reference' ? 'mono' : ''}>{v}</dd>
            </div>
          ))}
      </dl>

      {success && payment.receipt_url && (
        <a className="btn primary block" href={payment.receipt_url}>
          Download PDF Receipt (with QR Code)
        </a>
      )}
      {success && (
        <p className="fine-print muted">
          A confirmation email has been sent to {payment.email}.
        </p>
      )}
      <button className="btn ghost block" onClick={onReset}>
        Make another payment
      </button>
    </div>
  )
}

/* ------------------------------- Admin view ------------------------------ */

function LoginBox({ onLoggedIn }) {
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState(null)
  const [busy, setBusy] = useState(false)

  async function submit(e) {
    e.preventDefault()
    setBusy(true)
    setError(null)
    try {
      await api('/admin/login/', { method: 'POST', body: { username, password } })
      onLoggedIn()
    } catch (err) {
      setError(err.message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="card login-card">
      <h2>Admin Sign In</h2>
      <form onSubmit={submit}>
        <label className="field">
          <span>Username</span>
          <input value={username} onChange={(e) => setUsername(e.target.value)} autoFocus />
        </label>
        <label className="field">
          <span>Password</span>
          <input
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
          />
        </label>
        {error && <div className="alert">{error}</div>}
        <button className="btn primary block" disabled={busy}>
          {busy ? <Spinner /> : 'Sign in'}
        </button>
      </form>
    </div>
  )
}

function StatCards({ stats }) {
  return (
    <div className="stat-grid">
      <div className="stat-card highlight">
        <span>Total Collected</span>
        <strong>{naira(stats.total_collected)}</strong>
      </div>
      <div className="stat-card">
        <span>Successful</span>
        <strong>{stats.successful}</strong>
      </div>
      <div className="stat-card">
        <span>Pending</span>
        <strong>{stats.pending}</strong>
      </div>
      <div className="stat-card">
        <span>Failed</span>
        <strong>{stats.failed}</strong>
      </div>
    </div>
  )
}

/* Dynamic Multi-Color Department Bar Chart */
function DepartmentRevenueChart({ chartData }) {
  const [mode, setMode] = useState('revenue') // 'revenue' | 'count'

  if (!chartData || chartData.length === 0) return null

  const maxVal = Math.max(
    ...chartData.map((d) => (mode === 'revenue' ? Number(d.total) : d.count)),
    1,
  )

  return (
    <div className="card chart-card">
      <div className="chart-header">
        <div>
          <h3>Departmental Payment Breakdown</h3>
          <p className="muted">Real-time revenue and student payment distribution</p>
        </div>
        <div className="chart-toggle">
          <button
            className={`chart-toggle-btn ${mode === 'revenue' ? 'active' : ''}`}
            onClick={() => setMode('revenue')}
          >
            Revenue (NGN)
          </button>
          <button
            className={`chart-toggle-btn ${mode === 'count' ? 'active' : ''}`}
            onClick={() => setMode('count')}
          >
            Students Paid
          </button>
        </div>
      </div>

      <div className="chart-container">
        {chartData.map((item) => {
          const val = mode === 'revenue' ? Number(item.total) : item.count
          const heightPercent = Math.round((val / maxVal) * 100)
          return (
            <div className="chart-col" key={item.department_code}>
              <div className="chart-bar-wrap">
                <span className="chart-val-label">
                  {mode === 'revenue' ? naira(val) : `${val}`}
                </span>
                <div
                  className="chart-bar"
                  style={{
                    height: `${Math.max(heightPercent, 4)}%`,
                    backgroundColor: item.color,
                  }}
                  title={`${item.department_name} (${item.department_code}): ${naira(item.total)} (${item.count} students)`}
                />
              </div>
              <div className="chart-label">
                <strong>{item.department_code}</strong>
                <small title={item.department_name}>{item.department_name}</small>
              </div>
            </div>
          )
        })}
      </div>

      <div className="chart-legend">
        {chartData.map((item) => (
          <div className="legend-item" key={item.department_code}>
            <span className="legend-color" style={{ backgroundColor: item.color }} />
            <span>
              <b>{item.department_code}</b>: {naira(item.total)} ({item.count} paid)
            </span>
          </div>
        ))}
      </div>
    </div>
  )
}

/* Department & Course Management */
function DepartmentsManager({ onChanged }) {
  const [departments, setDepartments] = useState([])
  const [draft, setDraft] = useState({ code: '', name: '', subaccount_code: '' })
  const [edits, setEdits] = useState({})
  const [notice, setNotice] = useState(null)
  const [busy, setBusy] = useState(false)

  const load = useCallback(async () => {
    try {
      const data = await api('/admin/departments/')
      setDepartments(data.departments)
    } catch {
      // ignore
    }
  }, [])

  useEffect(() => {
    let cancelled = false
    api('/admin/departments/')
      .then((data) => {
        if (!cancelled) setDepartments(data.departments)
      })
      .catch(() => {})
    return () => {
      cancelled = true
    }
  }, [])


  async function createDepartment(e) {
    e.preventDefault()
    setBusy(true)
    setNotice(null)
    try {
      await api('/admin/departments/', {
        method: 'POST',
        body: {
          code: draft.code.trim().toUpperCase(),
          name: draft.name.trim(),
          subaccount_code: draft.subaccount_code.trim(),
        },
      })
      setDraft({ code: '', name: '', subaccount_code: '' })
      setNotice(`Department ${draft.code.toUpperCase()} created successfully.`)
      await load()
      onChanged()
    } catch (err) {
      setNotice(err.message || 'Could not create department.')
    } finally {
      setBusy(false)
    }
  }

  async function saveRow(dept) {
    const edit = edits[dept.id]
    if (!edit) return
    try {
      await api(`/admin/departments/${dept.id}/`, {
        method: 'PATCH',
        body: edit,
      })
      setEdits((prev) => {
        const next = { ...prev }
        delete next[dept.id]
        return next
      })
      setNotice(`Updated ${dept.code}`)
      await load()
      onChanged()
    } catch (err) {
      setNotice(err.message || 'Could not update department.')
    }
  }

  async function removeDepartment(dept) {
    if (!window.confirm(`Delete department ${dept.code} - ${dept.name}?`)) return
    try {
      await api(`/admin/departments/${dept.id}/`, { method: 'DELETE' })
      setNotice(`Deleted ${dept.code}`)
      await load()
      onChanged()
    } catch (err) {
      setNotice(err.message || 'Could not delete department.')
    }
  }

  return (
    <section className="panel">
      <div className="panel-head">
        <div>
          <h3>Departments &amp; Paystack Accounts</h3>
          <p className="muted">
            Add new courses and assign department-specific Paystack Subaccounts to route funds directly.
          </p>
        </div>
      </div>

      <form className="dept-draft" onSubmit={createDepartment}>
        <input
          type="text"
          placeholder="Code (e.g. ICH, MCB)"
          value={draft.code}
          maxLength={10}
          required
          onChange={(e) => setDraft({ ...draft, code: e.target.value.toUpperCase() })}
        />
        <input
          type="text"
          placeholder="Full Course Name (e.g. Industrial Chemistry)"
          value={draft.name}
          required
          onChange={(e) => setDraft({ ...draft, name: e.target.value })}
        />
        <input
          type="text"
          placeholder="Paystack Subaccount (e.g. ACCT_xxxxxxxxx)"
          value={draft.subaccount_code}
          onChange={(e) => setDraft({ ...draft, subaccount_code: e.target.value })}
        />
        <button className="btn primary" disabled={busy} type="submit">
          Add Department
        </button>
      </form>

      {notice && <div className="notice">{notice}</div>}

      <table className="table">
        <thead>
          <tr>
            <th>Code</th>
            <th>Department / Course Name</th>
            <th>Paystack Subaccount (Split Payout)</th>
            <th>Active</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          {departments.map((d) => {
            const edit = edits[d.id] || {}
            const dirty = Object.keys(edit).length > 0
            return (
              <tr key={d.id}>
                <td>
                  <strong>{d.code}</strong>
                </td>
                <td>
                  <input
                    className="inline-input"
                    type="text"
                    value={edit.name !== undefined ? edit.name : d.name}
                    onChange={(e) =>
                      setEdits((prev) => ({
                        ...prev,
                        [d.id]: { ...prev[d.id], name: e.target.value },
                      }))
                    }
                  />
                </td>
                <td>
                  <input
                    className="inline-input mono"
                    type="text"
                    placeholder="None (Main Account)"
                    value={
                      edit.subaccount_code !== undefined
                        ? edit.subaccount_code
                        : d.subaccount_code
                    }
                    onChange={(e) =>
                      setEdits((prev) => ({
                        ...prev,
                        [d.id]: { ...prev[d.id], subaccount_code: e.target.value },
                      }))
                    }
                  />
                </td>
                <td>
                  <input
                    type="checkbox"
                    checked={edit.is_active !== undefined ? edit.is_active : d.is_active}
                    onChange={(e) =>
                      setEdits((prev) => ({
                        ...prev,
                        [d.id]: { ...prev[d.id], is_active: e.target.checked },
                      }))
                    }
                  />
                </td>
                <td className="actions-cell">
                  <button className="btn small" disabled={!dirty} onClick={() => saveRow(d)}>
                    Save
                  </button>
                  <button className="btn small danger" onClick={() => removeDepartment(d)}>
                    Delete
                  </button>
                </td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </section>
  )
}

function FeesManager({ meta, onChanged }) {
  const [fees, setFees] = useState([])
  const [edits, setEdits] = useState({})
  const [draft, setDraft] = useState({
    department: meta.departments[0]?.code || '',
    level: 100,
    amount: '',
    description: 'Departmental Dues',
  })
  const [notice, setNotice] = useState(null)

  const load = useCallback(async () => {
    const data = await api('/admin/fees/')
    setFees(data.fees)
  }, [])

  useEffect(() => {
    let cancelled = false
    api('/admin/fees/')
      .then((data) => {
        if (!cancelled) setFees(data.fees)
      })
      .catch(() => {})
    return () => {
      cancelled = true
    }
  }, [])

  async function saveRow(fee) {
    const edit = edits[fee.id]
    if (!edit) return
    try {
      await api(`/admin/fees/${fee.id}/`, {
        method: 'PATCH',
        body: {
          ...(edit.amount !== undefined ? { amount: edit.amount } : {}),
          ...(edit.is_active !== undefined ? { is_active: edit.is_active } : {}),
        },
      })
      setEdits((e) => {
        const next = { ...e }
        delete next[fee.id]
        return next
      })
      setNotice(`Saved ${fee.department_name} ${fee.level}L`)
      await load()
      onChanged()
    } catch (err) {
      setNotice(err.message)
    }
  }

  async function upsertDraft(e) {
    e.preventDefault()
    try {
      await api('/admin/fees/', { method: 'POST', body: draft })
      setNotice(`${draft.department} ${draft.level}L fee updated`)
      setDraft((d) => ({ ...d, amount: '', description: 'Departmental Dues' }))
      await load()
      onChanged()
    } catch (err) {
      setNotice(err.message)
    }
  }

  async function removeFee(fee) {
    if (!window.confirm(`Delete ${fee.department_name} ${fee.level}L fee?`)) return
    try {
      await api(`/admin/fees/${fee.id}/`, { method: 'DELETE' })
      setNotice('Fee deleted')
      await load()
      onChanged()
    } catch (err) {
      setNotice(err.message)
    }
  }

  const grouped = useMemo(() => {
    const map = {}
    for (const fee of fees) {
      ;(map[fee.department_name] ||= []).push(fee)
    }
    return map
  }, [fees])

  return (
    <section className="panel">
      <h3>Fees Management</h3>
      <form className="fee-draft" onSubmit={upsertDraft}>
        <select
          value={draft.department}
          onChange={(e) => setDraft({ ...draft, department: e.target.value })}
        >
          {meta.departments.map((d) => (
            <option key={d.code} value={d.code}>
              {d.name} ({d.code})
            </option>
          ))}
        </select>
        <select
          value={draft.level}
          onChange={(e) => setDraft({ ...draft, level: Number(e.target.value) })}
        >
          {meta.levels.map((l) => (
            <option key={l} value={l}>
              {l} Level
            </option>
          ))}
        </select>
        <input
          type="number"
          min="1"
          step="0.01"
          placeholder="Amount (NGN)"
          value={draft.amount}
          onChange={(e) => setDraft({ ...draft, amount: e.target.value })}
        />
        <input
          type="text"
          placeholder="Description"
          value={draft.description}
          onChange={(e) => setDraft({ ...draft, description: e.target.value })}
        />
        <button className="btn primary" type="submit">
          Set Fee
        </button>
      </form>

      {notice && <div className="notice">{notice}</div>}

      <table className="table">
        <thead>
          <tr>
            <th>Department</th>
            <th>Level</th>
            <th>Amount (NGN)</th>
            <th>Active</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          {Object.entries(grouped).map(([deptName, rows]) =>
            rows.map((fee, index) => {
              const edit = edits[fee.id] || {}
              const dirty = Object.keys(edit).length > 0
              return (
                <tr key={fee.id} className={index === 0 ? 'group-start' : ''}>
                  <td>{index === 0 ? deptName : ''}</td>
                  <td>
                    <Badge tone={fee.is_active ? 'green' : 'grey'}>{fee.level}L</Badge>
                  </td>
                  <td>
                    <input
                      className="inline-input"
                      type="number"
                      min="1"
                      step="0.01"
                      value={edit.amount ?? Number(fee.amount)}
                      onChange={(e) =>
                        setEdits((prev) => ({
                          ...prev,
                          [fee.id]: { ...prev[fee.id], amount: e.target.value },
                        }))
                      }
                    />
                  </td>
                  <td>
                    <input
                      type="checkbox"
                      checked={edit.is_active ?? fee.is_active}
                      onChange={(e) =>
                        setEdits((prev) => ({
                          ...prev,
                          [fee.id]: { ...prev[fee.id], is_active: e.target.checked },
                        }))
                      }
                    />
                  </td>
                  <td className="actions-cell">
                    <button
                      className="btn small"
                      disabled={!dirty}
                      onClick={() => saveRow(fee)}
                    >
                      Save
                    </button>
                    <button className="btn small danger" onClick={() => removeFee(fee)}>
                      Delete
                    </button>
                  </td>
                </tr>
              )
            }),
          )}
        </tbody>
      </table>
    </section>
  )
}

function PaymentsReport({ meta }) {
  const [filters, setFilters] = useState({ status: '', department: '', q: '' })
  const [rows, setRows] = useState(null)

  const query = useMemo(() => {
    const params = new URLSearchParams()
    if (filters.status) params.set('status', filters.status)
    if (filters.department) params.set('department', filters.department)
    if (filters.q.trim()) params.set('q', filters.q.trim())
    return params.toString()
  }, [filters])

  useEffect(() => {
    let cancelled = false
    const timer = setTimeout(() => {
      api(`/admin/payments/${query ? `?${query}` : ''}`)
        .then((data) => {
          if (!cancelled) setRows(data.payments)
        })
        .catch(() => {})
    }, 250)
    return () => {
      cancelled = true
      clearTimeout(timer)
    }
  }, [query])

  return (
    <section className="panel">
      <div className="panel-head">
        <h3>Payment Reports</h3>
        <a className="btn ghost" href="/api/admin/payments/export/">
          Export CSV
        </a>
      </div>

      <div className="filter-row">
        <input
          placeholder="Search name, matric, email…"
          value={filters.q}
          onChange={(e) => setFilters({ ...filters, q: e.target.value })}
        />
        <select
          value={filters.status}
          onChange={(e) => setFilters({ ...filters, status: e.target.value })}
        >
          <option value="">All statuses</option>
          <option value="successful">Successful</option>
          <option value="pending">Pending</option>
          <option value="failed">Failed</option>
        </select>
        <select
          value={filters.department}
          onChange={(e) => setFilters({ ...filters, department: e.target.value })}
        >
          <option value="">All departments</option>
          {meta.departments.map((d) => (
            <option key={d.code} value={d.code}>
              {d.name} ({d.code})
            </option>
          ))}
        </select>
      </div>

      <table className="table report-table">
        <thead>
          <tr>
            <th>Date</th>
            <th>Student</th>
            <th>Matric</th>
            <th>Dept / Level</th>
            <th>Amount</th>
            <th>Status</th>
            <th>Receipt</th>
          </tr>
        </thead>
        <tbody>
          {rows === null && (
            <tr>
              <td colSpan="7" className="empty">
                <Spinner /> Loading…
              </td>
            </tr>
          )}
          {rows?.length === 0 && (
            <tr>
              <td colSpan="7" className="empty muted">
                No payments match these filters.
              </td>
            </tr>
          )}
          {rows?.map((p) => (
            <tr key={p.reference}>
              <td>{p.created_at ? new Date(p.created_at).toLocaleString() : '—'}</td>
              <td>{p.full_name}</td>
              <td className="mono">{p.matric_number}</td>
              <td>
                {p.department} · {p.level}L
              </td>
              <td>{naira(p.amount)}</td>
              <td>
                <Badge
                  tone={
                    p.status === 'successful' ? 'green' : p.status === 'pending' ? 'orange' : 'red'
                  }
                >
                  {p.status}
                </Badge>
              </td>
              <td>
                {p.receipt_url ? (
                  <a href={p.receipt_url}>PDF</a>
                ) : (
                  <span className="muted">—</span>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  )
}

function SessionManager({ meta, reloadMeta }) {
  const [info, setInfo] = useState(null)
  const [draft, setDraft] = useState('')
  const [notice, setNotice] = useState(null)
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    let cancelled = false
    api('/admin/session/')
      .then((data) => {
        if (cancelled) return
        setInfo(data)
        setDraft(String(data.session_start))
      })
      .catch(() => {})
    return () => {
      cancelled = true
    }
  }, [meta.session])

  async function save(e) {
    e.preventDefault()
    setBusy(true)
    setNotice(null)
    try {
      const data = await api('/admin/session/', {
        method: 'POST',
        body: { session_start: Number(draft) },
      })
      setInfo(data)
      await reloadMeta()
    } catch (err) {
      setNotice(err.message || 'Could not save the session.')
    } finally {
      setBusy(false)
    }
  }

  async function revert() {
    setBusy(true)
    setNotice(null)
    try {
      const data = await api('/admin/session/', { method: 'DELETE' })
      setInfo(data)
      await reloadMeta()
    } catch (err) {
      setNotice(err.message || 'Could not revert to automatic session.')
    } finally {
      setBusy(false)
    }
  }

  return (
    <section className="panel">
      <h3>Active Session</h3>
      <p className="muted">
        Controls the session shown to students and how their level is derived from
        matric numbers. Pin it manually when your session starts on a different
        month (e.g. November).
      </p>
      <div className="session-row">
        <strong>{meta.session}</strong>
        {info && (
          <Badge tone={info.manual ? 'green' : 'grey'}>
            {info.manual ? 'Set manually' : 'Automatic'}
          </Badge>
        )}
      </div>
      <form className="session-form" onSubmit={save}>
        <input
          type="number"
          min="2000"
          max={new Date().getFullYear() + 1}
          step="1"
          aria-label="Session start year"
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
        />
        <button className="btn primary" disabled={busy} type="submit">
          Set Session
        </button>
        {info?.manual && (
          <button className="btn ghost" disabled={busy} type="button" onClick={revert}>
            Use automatic
          </button>
        )}
      </form>
      {notice && <div className="notice">{notice}</div>}
    </section>
  )
}

function ReceiptChecker() {
  const [code, setCode] = useState('')
  const [result, setResult] = useState(null)
  const [busy, setBusy] = useState(false)

  async function submit(e) {
    e.preventDefault()
    setBusy(true)
    try {
      setResult(
        await api(`/admin/receipts/verify/?code=${encodeURIComponent(code.trim())}`),
      )
    } catch (err) {
      setResult({ valid: false, reason: err.message })
    } finally {
      setBusy(false)
    }
  }

  const p = result?.payment
  return (
    <section className="panel">
      <h3>Verify Receipt</h3>
      <p className="muted">
        Paste the Receipt No. or Reference printed on a student's PDF (or scan QR code) to confirm
        it matches a real, successful payment.
      </p>
      <form className="receipt-check" onSubmit={submit}>
        <input
          value={code}
          onChange={(e) => {
            setCode(e.target.value)
            setResult(null)
          }}
          placeholder="e.g. 32091512-92cc-4d79-… or STECH-F688C4958DA6-…"
          spellCheck="false"
        />
        <button className="btn primary" disabled={busy || !code.trim()} type="submit">
          {busy ? <Spinner /> : 'Check receipt'}
        </button>
      </form>

      {result && !result.valid && (
        <div className="receipt-result bad">
          <Badge tone="red">Not a valid paid receipt</Badge>
          {result.reason && <p className="muted">{result.reason}</p>}
        </div>
      )}

      {result?.valid && p && (
        <div className="receipt-result ok">
          <Badge tone="green">Valid receipt — payment confirmed</Badge>
          <dl className="detail-list">
            {[
              ['Student', p.full_name],
              ['Matric No.', p.matric_number],
              ['Department', `${p.department} · ${p.level} Level`],
              ['Session', p.session],
              ['Amount', naira(p.amount)],
              ['Paid at', p.paid_at ? new Date(p.paid_at).toLocaleString() : '—'],
            ].map(([k, v]) => (
              <div className="detail-row" key={k}>
                <dt>{k}</dt>
                <dd className={k.includes('No.') ? 'mono' : ''}>{v}</dd>
              </div>
            ))}
          </dl>
          {p.receipt_url && (
            <a className="btn ghost small" href={p.receipt_url}>
              Open original PDF
            </a>
          )}
        </div>
      )}
    </section>
  )
}

function AdminDashboard({ meta, reloadMeta, onLoggedOut }) {
  const [stats, setStats] = useState(null)
  const [adminTab, setAdminTab] = useState('overview') // 'overview' | 'departments' | 'fees' | 'payments' | 'session' | 'verify'

  const loadStats = useCallback(
    () => api('/admin/stats/').then(setStats).catch(() => {}),
    [],
  )
  useEffect(() => {
    loadStats()
  }, [loadStats])

  async function logout() {
    await api('/admin/logout/', { method: 'POST' }).catch(() => {})
    onLoggedOut()
  }

  const handleDataChanged = () => {
    loadStats()
    reloadMeta()
  }

  return (
    <div className="admin-wrap">
      <div className="admin-head">
        <h2>Admin Dashboard</h2>
        <button className="btn ghost" onClick={logout}>
          Log out
        </button>
      </div>

      {stats && <StatCards stats={stats} />}

      <nav className="admin-nav">
        <button
          className={`admin-nav-btn ${adminTab === 'overview' ? 'active' : ''}`}
          onClick={() => setAdminTab('overview')}
        >
          Overview &amp; Charts
        </button>
        <button
          className={`admin-nav-btn ${adminTab === 'departments' ? 'active' : ''}`}
          onClick={() => setAdminTab('departments')}
        >
          Courses &amp; Subaccounts
        </button>
        <button
          className={`admin-nav-btn ${adminTab === 'fees' ? 'active' : ''}`}
          onClick={() => setAdminTab('fees')}
        >
          Fee Schedules
        </button>
        <button
          className={`admin-nav-btn ${adminTab === 'payments' ? 'active' : ''}`}
          onClick={() => setAdminTab('payments')}
        >
          Payment Records
        </button>
        <button
          className={`admin-nav-btn ${adminTab === 'session' ? 'active' : ''}`}
          onClick={() => setAdminTab('session')}
        >
          Academic Session
        </button>
        <button
          className={`admin-nav-btn ${adminTab === 'verify' ? 'active' : ''}`}
          onClick={() => setAdminTab('verify')}
        >
          Receipt Verification
        </button>
      </nav>

      {adminTab === 'overview' && stats && (
        <>
          <DepartmentRevenueChart chartData={stats.chart_data} />
          <section className="panel breakdown-grid">
            <div>
              <h4>Revenue by Department</h4>
              <ul className="breakdown">
                {stats.by_department.length === 0 && (
                  <li className="muted">No successful payments yet.</li>
                )}
                {stats.by_department.map((row) => (
                  <li key={row.department_code}>
                    <span>
                      {row.department_name} ({row.department_code})
                    </span>
                    <strong>{naira(row.total)}</strong>
                  </li>
                ))}
              </ul>
            </div>
            <div>
              <h4>Revenue by Level</h4>
              <ul className="breakdown">
                {stats.by_level.length === 0 && (
                  <li className="muted">No successful payments yet.</li>
                )}
                {stats.by_level.map((row) => (
                  <li key={row.level}>
                    <span>{row.level} Level</span>
                    <strong>{naira(row.total)}</strong>
                  </li>
                ))}
              </ul>
            </div>
          </section>
        </>
      )}

      {adminTab === 'departments' && (
        <DepartmentsManager onChanged={handleDataChanged} />
      )}

      {adminTab === 'fees' && (
        <FeesManager meta={meta} onChanged={handleDataChanged} />
      )}

      {adminTab === 'payments' && (
        <PaymentsReport meta={meta} />
      )}

      {adminTab === 'session' && (
        <SessionManager meta={meta} reloadMeta={reloadMeta} />
      )}

      {adminTab === 'verify' && (
        <ReceiptChecker />
      )}
    </div>
  )
}

function AdminView({ meta, reloadMeta }) {
  const [authed, setAuthed] = useState(null)

  useEffect(() => {
    api('/admin/me/')
      .then((data) => setAuthed(Boolean(data.authenticated)))
      .catch(() => setAuthed(false))
  }, [])

  if (authed === null) return <Spinner />
  if (!authed) return <LoginBox onLoggedIn={() => setAuthed(true)} />
  return <AdminDashboard meta={meta} reloadMeta={reloadMeta} onLoggedOut={() => setAuthed(false)} />
}

/* ---------------------------------- App ---------------------------------- */

export default function App() {
  const [tab, setTab] = useState('pay')
  const [meta, setMeta] = useState(null)
  const [result, setResult] = useState(null)
  const handledRef = useRef(false)

  const loadMeta = useCallback(
    () =>
      api('/meta/').then(setMeta).catch((err) => setResult({ error: err.message })),
    [],
  )

  useEffect(() => {
    loadMeta()
  }, [loadMeta])

  useEffect(() => {
    if (handledRef.current) return
    handledRef.current = true
    const params = new URLSearchParams(window.location.search)
    const reference = params.get('reference')
    if (!reference) return
    history.replaceState(null, '', window.location.pathname)
    api('/payments/verify/', { method: 'POST', body: { reference } })
      .then((data) => setResult({ payment: data.payment }))
      .catch((err) => setResult({ error: err.message }))
  }, [])

  return (
    <div className="app">
      <header className="topbar">
        <div className="brand">
          <img src={logo} alt="STechPay logo" className="brand-logo" />
          <div>
            <strong>STechPay</strong>
            <small>School of Science &amp; Technology</small>
          </div>
        </div>
        <nav className="tabs">
          <button
            className={tab === 'pay' ? 'tab active' : 'tab'}
            onClick={() => setTab('pay')}
          >
            Make Payment
          </button>
          <button
            className={tab === 'admin' ? 'tab active' : 'tab'}
            onClick={() => setTab('admin')}
          >
            Admin Dashboard
          </button>
        </nav>
      </header>

      <main className="content">
        {result && (
          <ResultCard
            payment={result.payment}
            onReset={() => setResult(null)}
          />
        )}

        {!result && tab === 'pay' &&
          (meta ? (
            <PaymentForm meta={meta} onSuccess={(payment) => setResult({ payment })} />
          ) : (
            <Spinner />
          ))}

        {!result && tab === 'admin' &&
          (meta ? <AdminView meta={meta} reloadMeta={loadMeta} /> : <Spinner />)}

        {result?.error && (
          <div className="card result-card bad">
            <h2>Something went wrong</h2>
            <p className="muted">{result.error}</p>
          </div>
        )}
      </main>

      <footer className="footer muted">
        STechPay · Departmental Payment System · Powered by Paystack
      </footer>
    </div>
  )
}

