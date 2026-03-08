import { create } from 'zustand'

interface AuthState {
  token: string | null
  isAuthenticated: boolean
  user: { username: string; email: string } | null
  login: (token: string, user?: { username: string; email: string }) => void
  logout: () => void
  initFromStorage: () => void
}

export const useAuthStore = create<AuthState>((set) => ({
  token: null,
  isAuthenticated: false,
  user: null,

  login: (token, user) => {
    localStorage.setItem('auth_token', token)
    if (user) localStorage.setItem('auth_user', JSON.stringify(user))
    set({ token, isAuthenticated: true, user: user ?? null })
  },

  logout: () => {
    localStorage.removeItem('auth_token')
    localStorage.removeItem('auth_user')
    localStorage.removeItem('ws_token')
    set({ token: null, isAuthenticated: false, user: null })
  },

  initFromStorage: () => {
    const token = localStorage.getItem('auth_token')
    const userRaw = localStorage.getItem('auth_user')
    if (token) {
      const user = userRaw ? JSON.parse(userRaw) : null
      set({ token, isAuthenticated: true, user })
    }
  },
}))
