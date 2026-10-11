import { createBrowserRouter } from 'react-router'
import { RouterProvider } from 'react-router/dom'
import { PageTransition } from './components/PageTransition.tsx'
import { DevConsole } from './dev/DevConsole.tsx'
import { LivePage } from './pages/LivePage.tsx'
import { NotFoundPage } from './pages/NotFoundPage.tsx'
import { ReportPage } from './pages/ReportPage.tsx'
import { SetupPage } from './pages/SetupPage.tsx'
import { VerdictPage } from './pages/VerdictPage.tsx'

/**
 * Routes (frontend.md):
 *   /                     Setup: name, type, role → creates the session
 *   /session/:id          Live interview: camera, questions, vitals
 *   /session/:id/report   The gods' record: replay + per-question feedback
 *   /session/:id/verdict  The judgement: stand again, or be dismissed through the floor
 *   /dev                  Dev Console: raw wiring test, every event logged
 * Vercel serves index.html for every path (vercel.json), so deep links work.
 *
 * Every route of the trial sits under PageTransition, which stages the move from one chamber of
 * the palace to the next. /dev stays outside it: it's a wiring test, not part of the trial.
 */
const router = createBrowserRouter([
  {
    element: <PageTransition />,
    children: [
      { path: '/', element: <SetupPage /> },
      { path: '/session/:id', element: <LivePage /> },
      { path: '/session/:id/report', element: <ReportPage /> },
      { path: '/session/:id/verdict', element: <VerdictPage /> },
      { path: '*', element: <NotFoundPage /> },
    ],
  },
  { path: '/dev', element: <DevConsole /> },
])

function App() {
  return <RouterProvider router={router} />
}

export default App
