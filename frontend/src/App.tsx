import { createBrowserRouter } from 'react-router'
import { RouterProvider } from 'react-router/dom'
import { DevConsole } from './dev/DevConsole.tsx'
import { LivePage } from './pages/LivePage.tsx'
import { NotFoundPage } from './pages/NotFoundPage.tsx'
import { ReportPage } from './pages/ReportPage.tsx'
import { SetupPage } from './pages/SetupPage.tsx'

/**
 * Routes (frontend.md):
 *   /                     Setup: name, type, role → creates the session
 *   /session/:id          Live interview: camera, questions, vitals
 *   /session/:id/report   Report + replay (opens automatically when the interview ends)
 *   /dev                  Dev Console: raw wiring test, every event logged
 * Vercel serves index.html for every path (vercel.json), so deep links work.
 */
const router = createBrowserRouter([
  { path: '/', element: <SetupPage /> },
  { path: '/session/:id', element: <LivePage /> },
  { path: '/session/:id/report', element: <ReportPage /> },
  { path: '/dev', element: <DevConsole /> },
  { path: '*', element: <NotFoundPage /> },
])

function App() {
  return <RouterProvider router={router} />
}

export default App
