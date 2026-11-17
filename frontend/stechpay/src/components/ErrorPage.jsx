import { Component, useEffect, useState } from 'react'

export default function ErrorPage({ title = 'We couldn’t complete that request', message = 'Please try again shortly.', onRetry, status }) {
  return <section className="card error-page" role="alert">
    <div className="auth-symbol" aria-hidden="true">!</div>
    {status && <p className="auth-step">ERROR {status}</p>}
    <h2>{title}</h2><p className="muted">{message}</p>
    <div className="error-actions">{onRetry && <button className="btn primary" onClick={onRetry}>Try again</button>}
      <a className="btn ghost" href="/">Return to home</a></div>
  </section>
}
export function NetworkNotice() {
  const [offline, setOffline] = useState(!navigator.onLine)
  useEffect(() => {
    const update = () => setOffline(!navigator.onLine)
    window.addEventListener('online', update); window.addEventListener('offline', update)
    return () => { window.removeEventListener('online', update); window.removeEventListener('offline', update) }
  }, [])
  return offline && <div className="offline-notice" role="status">You’re offline. Your entries are still here. Reconnect, then try again.</div>
}
export class ErrorBoundary extends Component {
  state = { failed: false }
  static getDerivedStateFromError() { return { failed: true } }
  render() {
    return this.state.failed ? <ErrorPage title="Something went wrong" message="Please reload STechPay to continue." onRetry={() => window.location.reload()} /> : this.props.children
  }
}
