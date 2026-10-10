import { DevConsole } from './dev/DevConsole.tsx'

/**
 * Root component. For now it renders the Dev Console (a wiring test page).
 * Once the design tokens land, this becomes the router: / (Setup),
 * /session/:id (Live), /session/:id/report (Report).
 */
function App() {
  return <DevConsole />
}

export default App
