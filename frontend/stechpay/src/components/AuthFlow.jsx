import { useEffect, useRef, useState } from 'react'
import { api } from '../lib/api'
import { navigate } from '../lib/navigation'

export default function AuthFlow({ onLoggedIn }) {
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [code, setCode] = useState('')
  const [challenge, setChallenge] = useState(null)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [busy, setBusy] = useState(false)
  const [expired, setExpired] = useState(false)
  const [now, setNow] = useState(() => Date.now())
  const codeInput = useRef(null)
  const verifying = window.location.pathname.replace(/\/$/, '') === '/sign-in/verify'
  useEffect(() => {
    if (!verifying) return
    let active = true
    api('/admin/login/challenge/').then(data => { if (active) { setChallenge(data); setNow(Date.now()) } })
      .catch(err => { if (active) { setError(err.message); setExpired(err.data?.code === 'challenge_expired') } })
    const timer = setInterval(() => setNow(Date.now()), 1000)
    return () => { active = false; clearInterval(timer) }
  }, [verifying])
  const secondsLeft = challenge ? Math.max(0, Math.ceil((Date.parse(challenge.expires_at) - now) / 1000)) : null
  const resendWait = challenge ? Math.max(0, Math.ceil((Date.parse(challenge.resend_at) - now) / 1000)) : 0
  const ended = expired || secondsLeft === 0
  async function submit(event) {
    event.preventDefault(); setBusy(true); setError(''); setNotice('')
    try {
      const result = await api(verifying ? '/admin/login/confirm/' : '/admin/login/', {
        method: 'POST', body: verifying ? { code } : { username, password },
      })
      if (result.mfa_required) { setPassword(''); navigate('/sign-in/verify') }
      else { navigate('/dashboard'); onLoggedIn() }
    } catch (err) {
      setError(err.message)
      if (err.data?.code === 'challenge_expired') setExpired(true)
      codeInput.current?.focus()
    } finally { setBusy(false) }
  }
  async function resend() {
    setBusy(true); setError(''); setNotice('')
    try {
      setChallenge(await api('/admin/login/challenge/', { method: 'POST' }))
      setNow(Date.now()); setCode(''); setNotice('A new code has been sent. Use the latest email.'); codeInput.current?.focus()
    } catch (err) { setError(err.message); if (err.data?.code === 'challenge_expired') setExpired(true) }
    finally { setBusy(false) }
  }
  return <section className="card login-card auth-card" aria-labelledby="auth-title">
    <div className="auth-step">ADMIN ACCESS · STEP {verifying ? '2' : '1'} OF 2</div>
    <div className="auth-symbol" aria-hidden="true">{verifying ? '✉' : '◇'}</div>
    <h2 id="auth-title">{verifying ? 'Check your email' : 'Welcome back'}</h2>
    <p className="muted">{verifying ? 'Enter the six-digit sign-in code sent to your administrator email.' : 'Sign in to manage payments and your school settings.'}</p>
    <form onSubmit={submit}>
      {verifying ? <>
        <label className="field"><span>Verification code</span>
          <input ref={codeInput} className="otp-input" aria-describedby="code-help" value={code}
            onChange={e => setCode(e.target.value.replace(/\D/g, '').slice(0, 6))}
            inputMode="numeric" autoComplete="one-time-code" pattern="[0-9]{6}" maxLength={6}
            placeholder="000000" autoFocus required disabled={ended || busy} />
        </label>
        <p className="muted" id="code-help">{ended ? 'This code has expired. Start sign-in again.' : secondsLeft === null ? 'You can paste the full code from your email.' : `Code expires in ${Math.floor(secondsLeft / 60)}:${String(secondsLeft % 60).padStart(2, '0')}`}</p>
      </> : <>
        <label className="field"><span>Username</span><input value={username} onChange={e => setUsername(e.target.value)} autoComplete="username" autoFocus required /></label>
        <label className="field"><span>Password</span><input type="password" value={password} onChange={e => setPassword(e.target.value)} autoComplete="current-password" required /></label>
      </>}
      {error && <div className="alert" role="alert">{error}</div>}
      {notice && <p role="status">{notice}</p>}
      <button className="btn primary block" disabled={busy || (verifying && (ended || code.length !== 6))}>{busy ? 'Please wait…' : verifying ? 'Verify and sign in' : 'Continue'}</button>
      {verifying && <>
        <button className="btn ghost block" type="button" onClick={resend} disabled={busy || ended || !challenge || resendWait > 0 || !challenge.resends_remaining}>
          {resendWait > 0 ? `Resend code in ${resendWait}s` : 'Resend code'}
        </button>
        {!challenge && !ended && <button className="btn ghost block" type="button" onClick={() => window.location.reload()}>Retry connection</button>}
        <button className="btn ghost block" type="button" disabled={busy} onClick={() => navigate('/sign-in')}>Back to sign in</button>
      </>}
    </form>
  </section>
}
