/**
 * Moving between chambers of the palace.
 *
 * A layout route (App.tsx) that stages every navigation: the new chamber resolves out of the dark
 * while a shaft of gold light crosses the hall. Keyed on the pathname, so each route change replays
 * it exactly once.
 *
 * Two deliberate constraints:
 *  - The wrapper animates OPACITY only. A transform or filter here would become the containing
 *    block for the `fixed` overlays the pages render inside it (AnalyzingScreen, the start-error
 *    screen), which would pin them to the wrapper instead of the viewport.
 *  - The sweep is therefore a SIBLING of the wrapper, not a child, so it can move freely without
 *    affecting anything the pages lay out.
 */
import { useLocation, Outlet } from 'react-router'

/** The shaft of gold that crosses the hall as the chamber changes. */
function LightSweep() {
  return (
    <div className="pointer-events-none fixed inset-0 z-[60] overflow-hidden" aria-hidden="true">
      <div
        className="animate-light-sweep absolute inset-y-0 -left-1/3 w-1/3 bg-[linear-gradient(100deg,transparent,rgb(212_175_55/0.16)_35%,rgb(255_243_196/0.42)_50%,rgb(212_175_55/0.16)_65%,transparent)]"
      />
    </div>
  )
}

export function PageTransition() {
  const { pathname } = useLocation()
  return (
    <>
      <div key={pathname} className="animate-page-enter">
        <Outlet />
      </div>
      {/* Keyed separately so the sweep restarts even when the wrapper is reused. */}
      <LightSweep key={`sweep:${pathname}`} />
    </>
  )
}
