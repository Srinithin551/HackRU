import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
// Self-hosted variable fonts (bundled by Vite: no network needed at the venue).
import '@fontsource-variable/geist'
import '@fontsource-variable/geist-mono'
import './index.css'
import App from './App.tsx'
import { APP_NAME } from './lib/constants.ts'
import { openCameraEarly } from './lib/cameraStream.ts'

// The tab/window title follows the one APP_NAME variable (index.html only has the initial value).
document.title = APP_NAME

// Open the webcam now, before any interview, so the Presage sensor starts after the page has the camera
// (see lib/cameraStream.ts: the other order crashed Presage).
openCameraEarly()

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <App />
  </StrictMode>,
)
