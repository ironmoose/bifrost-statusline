import { expect, test } from 'claude-code/testing'
import { compactLine, nextStatusFile } from './register'

const NOW = Date.parse('2026-10-19T20:00:00Z')
const USAGE = {
  context: { tokens: 62_000, window: 200_000, percent: 31 },
  rateLimits: [
    { kind: 'five_hour', percentUsed: 40.4, resetsAt: '2026-10-19T22:10:00Z' },
    { kind: 'seven_day', percentUsed: 12, resetsAt: '2026-10-22T03:00:00Z' },
  ],
}
const OWN = { session_id: 'mine', context_window: { used_percentage: 31 } }

test('shows context and both windows with countdowns', async () => {
  expect(compactLine(USAGE, NOW)).toBe('ctx 31% · 5h 40% 2h10m · 7d 12% 2d7h')
})

test('clears the status when nothing is known yet', async () => {
  expect(compactLine({ context: { window: 200_000 }, rateLimits: [] }, NOW)).toBeUndefined()
})

test('a prompt claims the status file from another session', async () => {
  const other = { session_id: 'other', context_window: { used_percentage: 80 } }
  expect(nextStatusFile(other, 'mine', true, () => OWN, USAGE)).toEqual(OWN)
})

test('a background session refreshes only the shared usage windows', async () => {
  const other = { session_id: 'other', context_window: { used_percentage: 80 } }
  const next = nextStatusFile(other, 'mine', false, () => OWN, USAGE)
  expect(next?.session_id).toBe('other')
  expect(next?.context_window).toEqual({ used_percentage: 80 })
  expect(next?.rate_limits).toEqual({
    five_hour: { used_percentage: 40.4, resets_at: 1792447800 },
    seven_day: { used_percentage: 12, resets_at: 1792638000 },
  })
})

test('the owning session or an empty file gets the full payload', async () => {
  expect(nextStatusFile({ session_id: 'mine' }, 'mine', false, () => OWN, USAGE)).toEqual(OWN)
  expect(nextStatusFile(undefined, 'mine', false, () => OWN, USAGE)).toEqual(OWN)
})

test('a measurement sets the status line and writes the status file', async ($, on) => {
  const statuses: (string | undefined)[] = []
  const writes: Record<string, string> = {}
  const moves: string[][] = []
  on('clock.now', () => ({ value: NOW }))
  on('env.get', () => ({ value: '/home/me' }))
  on('session.id', () => ({ value: 'mine' }))
  on('session.model', () => ({ value: 'claude-opus-5-5' }))
  on('session.root', () => ({ value: '/repo' }))
  on('fs.read', () => {
    throw new Error('missing')
  })
  on('fs.write', (_, e) => {
    writes[e.path] = e.text
    return { value: undefined }
  })
  on('process.run', (_, e) => {
    moves.push([...e.argv])
    return { value: { exitCode: 0, stdout: '', stderr: '' } }
  })
  on('session.measure', (_, e) => ({ changed: e.changed }))
  on('ui.status', (_, e) => {
    statuses.push(e.text)
    return { value: undefined }
  })

  await $.session.measure({ ...USAGE, changed: ['context'] })

  const staging = '/home/me/.claude/bifrost/claude-status.json.mine.tmp'
  expect(statuses).toEqual(['ctx 31% · 5h 40% 2h10m · 7d 12% 2d7h'])
  expect(JSON.parse(writes[staging]).model.display_name).toBe('claude-opus-5-5')
  expect(moves).toEqual([['mv', '-f', staging, '/home/me/.claude/bifrost/claude-status.json']])
})
