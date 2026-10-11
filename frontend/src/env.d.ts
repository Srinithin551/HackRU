// Typed Vite env vars. Documented in frontend/.env.example.
interface ImportMetaEnv {
  /** Backend origin, e.g. "http://localhost:8000". Empty = same origin via the Vite proxy. */
  readonly VITE_API_URL?: string
  /** "1" runs the app entirely against src/mocks/. */
  readonly VITE_MOCK?: string
  /** "0" starts with the camera preview off (the SmartSpectra app keeps the webcam). Toggle with "Show camera". */
  readonly VITE_CAMERA_PREVIEW?: string
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}
