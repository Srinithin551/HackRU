// Typed Vite env vars. Documented in frontend/.env.example.
interface ImportMetaEnv {
  /** Backend origin, e.g. "http://localhost:8000". Empty = same origin via the Vite proxy. */
  readonly VITE_API_URL?: string
  /** "1" runs the app entirely against src/mocks/. */
  readonly VITE_MOCK?: string
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}
