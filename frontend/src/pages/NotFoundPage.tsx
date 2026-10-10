import { Link } from 'react-router'
import { Brand, Button } from '../components/ui.tsx'

export function NotFoundPage() {
  return (
    <div className="bg-monitor grid min-h-dvh place-items-center p-gutter text-center">
      <div className="flex flex-col items-center gap-5">
        <Brand />
        <p className="text-title font-semibold">This page doesn&apos;t exist.</p>
        <Link to="/">
          <Button>Start a new session</Button>
        </Link>
      </div>
    </div>
  )
}
