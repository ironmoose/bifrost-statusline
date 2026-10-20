import type { EngineInterface, Register, SessionUsage } from 'claude-code'

const REFRESH_MS = 30_000
const STATUS_FILE = '.claude/bifrost/claude-status.json'
const WINDOW_LABELS: Record<string, string> = { five_hour: '5h', seven_day: '7d' }

type Figures = Pick<SessionUsage, 'context' | 'rateLimits'>
type StatusFile = { session_id?: string; [key: string]: unknown }

const until = (resetsAt: string, now: number) => {
  const minutes = Math.max(0, Math.round((Date.parse(resetsAt) - now) / 60_000))
  if (minutes >= 1440) return `${Math.floor(minutes / 1440)}d${Math.floor((minutes % 1440) / 60)}h`
  if (minutes >= 60) return `${Math.floor(minutes / 60)}h${minutes % 60}m`
  return `${minutes}m`
}

// The app's status slot fits ~40 characters, so: numbers only, no bars, model or git.
export const compactLine = (usage: Figures, now: number) => {
  const parts = usage.context.percent === undefined ? [] : [`ctx ${usage.context.percent}%`]
  for (const limit of usage.rateLimits) {
    const label = WINDOW_LABELS[limit.kind]
    if (!label) continue
    const reset = limit.resetsAt ? ` ${until(limit.resetsAt, now)}` : ''
    parts.push(`${label} ${Math.round(limit.percentUsed)}%${reset}`)
  }
  return parts.length ? parts.join(' · ') : undefined
}

const rateLimits = (usage: Figures) =>
  Object.fromEntries(
    usage.rateLimits.map(limit => [
      limit.kind,
      {
        used_percentage: limit.percentUsed,
        resets_at: limit.resetsAt ? Math.floor(Date.parse(limit.resetsAt) / 1000) : undefined,
      },
    ]),
  )

// The statusLine stdin shape, which the desktop strip already reads for Codex.
export const buildPayload = (sessionId: string, model: string, cwd: string, usage: Figures) => ({
  session_id: sessionId,
  model: { id: model, display_name: model },
  cwd,
  workspace: { current_dir: cwd },
  context_window: {
    used_percentage: usage.context.percent,
    total_input_tokens: usage.context.tokens,
    total_output_tokens: 0,
    context_window_size: usage.context.window,
  },
  rate_limits: rateLimits(usage),
})

// The session the person last prompted owns the file; others only refresh the account-wide windows.
export const nextStatusFile = (
  current: StatusFile | undefined,
  sessionId: string,
  isClaim: boolean,
  build: () => StatusFile,
  usage: Figures,
): StatusFile | undefined => {
  if (isClaim || !current || current.session_id === sessionId) return build()
  if (!usage.rateLimits.length) return undefined
  return { ...current, rate_limits: rateLimits(usage) }
}

const readStatusFile = async ($: EngineInterface, path: string) => {
  try {
    return JSON.parse(await $.fs.read(path)) as StatusFile
  } catch {
    return undefined
  }
}

const publish = async ($: EngineInterface, usage: Figures, isClaim: boolean) => {
  const [home, sessionId, model, cwd] = await Promise.all([
    $.env.get('HOME'),
    $.session.id(),
    $.session.model(),
    $.session.root(),
  ])
  if (!home) return
  const path = `${home}/${STATUS_FILE}`
  const current = await readStatusFile($, path)
  const next = nextStatusFile(current, sessionId, isClaim, () => buildPayload(sessionId, model, cwd, usage), usage)
  if (!next) return
  // Write then rename, so the strip never reads a half-written file.
  const staging = `${path}.${sessionId}.tmp`
  await $.fs.write(staging, JSON.stringify(next))
  await $.process.run(['mv', '-f', staging, path])
}

const refresh = async ($: EngineInterface, usage?: Figures, isClaim = false) => {
  const figures = usage ?? (await $.session.usage())
  $.ui.status(compactLine(figures, await $.clock.now()))
  await publish($, figures, isClaim)
}

export const register: Register = on => {
  on('session.start', async ($, e, next) => {
    const started = await next(e)
    await refresh($)
    $.clock.every(REFRESH_MS, () => void refresh($))
    return started
  })

  on('session.measure', async ($, e, next) => {
    await refresh($, e)
    return next(e)
  })

  on('prompt.submit', async ($, e, next) => {
    const submitted = await next(e)
    await refresh($, undefined, true)
    return submitted
  })
}
