import { useEffect, useState } from 'react'
import { api, downloadReceipt } from '../lib/api'
import { navigate } from '../lib/navigation'

export default function ReceiptRecovery() {
  const verifying = window.location.pathname.replace(/\/$/, '') === '/receipts/verify'
  const [reference, setReference] = useState(() => new URLSearchParams(window.location.search).get('ref') || '')
  const [challenge, setChallenge] = useState(null)
  const [receipt, setReceipt] = useState(null)
  const [code, setCode] = useState('')
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [busy, setBusy] = useState(false)
  const [expired, setExpired] = useState(false)
  const [now, setNow] = useState(() => Date.now())

  useEffect(() => {
    if (!verifying) return
    let active = true
    api('/receipts/recovery/challenge/').then(data => {
      if (active) { if (data.receipt_url) setReceipt(data); else setChallenge(data); setNow(Date.now()) }
    }).catch(err => { if (active) { setError(err.message); setExpired(err.data?.code === 'challenge_expired') } })
    const timer = setInterval(() => setNow(Date.now()), 1000)
    return () => { active = false; clearInterval(timer) }
  }, [verifying])

  const left = challenge ? Math.max(0, Math.ceil((Date.parse(challenge.expires_at) - now) / 1000)) : null
  const wait = challenge ? Math.max(0, Math.ceil((Date.parse(challenge.resend_at) - now) / 1000)) : 0
  const ended = expired || left === 0

  async function submit(event) {
    event.preventDefault(); setError(''); setNotice(''); setBusy(true)
    try {
      const data = await api(verifying ? '/receipts/recovery/confirm/' : '/receipts/recovery/', {
        method: 'POST', body: verifying ? { code } : { reference: reference.trim() },
      })
      if (verifying) setReceipt(data)
      else navigate('/receipts/verify')
    } catch (err) { setError(err.message); if (err.data?.code === 'challenge_expired') setExpired(true) }
    finally { setBusy(false) }
  }

  async function resend() {
    setBusy(true); setError(''); setNotice('')
    try {
      setChallenge(await api('/receipts/recovery/challenge/', { method: 'POST' }))
      setNow(Date.now()); setCode(''); setNotice('If this reference has an eligible receipt, a new code will be emailed. Use the latest code.')
    } catch (err) { setError(err.message); if (err.data?.code === 'challenge_expired') setExpired(true) }
    finally { setBusy(false) }
  }

  async function download() {
    setBusy(true); setError(''); setNotice('')
    try { await downloadReceipt(receipt.receipt_url); setNotice('Your PDF is ready. Open it from your downloads to print or save it.') }
    catch (err) { setError(err.message) }
    finally { setBusy(false) }
  }

  return <section className="card login-card auth-card" aria-labelledby="receipt-title">
    <div className="auth-step">RECEIPT RECOVERY</div>
    <div className="auth-symbol" aria-hidden="true">{receipt ? '✓' : verifying ? '✉' : '↓'}</div>
    <h2 id="receipt-title">{receipt ? 'Your receipt is ready' : verifying ? 'Verify your email' : 'Retrieve your receipt'}</h2>
    <p className="muted">{receipt ? 'Download another copy of your original receipt. No new payment is needed.'
      : verifying ? 'If this reference has an eligible receipt, a six-digit code will be sent to the email used for payment. Delivery may take a minute.'
      : 'Enter the payment reference from your confirmation email. We’ll verify access through the email used for payment.'}</p>
    {error && <div className="alert" role="alert">{error}</div>}
    {notice && <p role="status">{notice}</p>}
    {receipt ? <>
      <p className="muted">Download access lasts 10 minutes. You can request another code whenever you need a copy.</p>
      <button className="btn primary block" onClick={download} disabled={busy}>{busy ? 'Preparing PDF…' : 'Download PDF receipt'}</button>
      <button className="btn ghost block" onClick={() => navigate('/receipts')}>Retrieve another receipt</button>
    </> : <form onSubmit={submit}>
      {verifying ? <>
        <label className="field"><span>Verification code</span><input className="otp-input" value={code}
          onChange={event => setCode(event.target.value.replace(/\D/g, '').slice(0, 6))} autoFocus required
          autoComplete="one-time-code" inputMode="numeric" pattern="[0-9]{6}" maxLength={6} placeholder="000000" disabled={ended || busy} /></label>
        <p className="muted">{ended ? 'This verification has expired. Request a new code.' : left === null ? 'Paste the full code from your email.' : `Verification expires in ${Math.floor(left / 60)}:${String(left % 60).padStart(2, '0')}`}</p>
      </> : <label className="field"><span>Payment reference</span><input value={reference} onChange={event => setReference(event.target.value)}
        autoFocus required maxLength={100} pattern="[A-Za-z0-9_\-]+" autoComplete="off" placeholder="e.g. STECH-TEST-…" /></label>}
      <button className="btn primary block" disabled={busy || (verifying && (ended || code.length !== 6))}>{busy ? 'Please wait…' : verifying ? 'Verify code' : 'Send verification code'}</button>
      {verifying && <>
        <button className="btn ghost block" type="button" onClick={resend} disabled={busy || ended || !challenge || wait > 0 || !challenge.resends_remaining}>{wait > 0 ? `Resend code in ${wait}s` : 'Resend code'}</button>
        {!challenge && !ended && <button className="btn ghost block" type="button" onClick={() => window.location.reload()}>Retry connection</button>}
        <button className="btn ghost block" type="button" onClick={() => navigate('/receipts')}>Use another reference</button>
      </>}
    </form>}
    <p className="fine-print muted">Cannot access that email? Contact the administration for help recovering your receipt.</p>
  </section>
}
