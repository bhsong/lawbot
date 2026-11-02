import { useState } from 'react'
import { ApiError, NETWORK_ERROR, api } from '../api'

export function Login({ onDone }: { onDone: () => void }) {
  const [user, setUser] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  return (
    <main className="login">
      <form
        className="login-card"
        onSubmit={async (e) => {
          e.preventDefault()
          setBusy(true)
          setError('')
          try {
            await api.login(user, password)
            onDone()
          } catch (err) {
            setError(err instanceof ApiError ? err.message : NETWORK_ERROR)
          } finally {
            setBusy(false)
          }
        }}
      >
        <h1>노동법 챗봇</h1>
        <label>
          사용자명
          <input autoComplete="username" value={user} onChange={(e) => setUser(e.target.value)} required />
        </label>
        <label>
          비밀번호
          <input type="password" autoComplete="current-password" value={password} onChange={(e) => setPassword(e.target.value)} required />
        </label>
        {error && (
          <p className="error" role="alert">
            {error}
          </p>
        )}
        <button type="submit" className="primary" disabled={busy}>
          로그인
        </button>
      </form>
    </main>
  )
}
