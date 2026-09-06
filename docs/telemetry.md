# How the numbers work

## Refresh cadence

The usage snapshot, token counts and rate-limit percentages, refreshes once
per response. This is the same for Claude Code and Codex, so those numbers
hold steady between responses; they don't tick up mid-reply.

Reset countdowns are computed live from the last known reset time and keep
moving independent of the snapshot. In tmux (the Codex path), the renderer
re-checks every 5 seconds (`TMUX_STATUS_INTERVAL`); the countdown itself is
minute-granular.

For Claude Code, `refreshInterval` (minimum 1 second) re-runs the script on a
timer, so countdowns keep ticking while you're idle. Claude Code also
debounces status updates by about 300ms and cancels an in-flight script if a
newer update arrives before it finishes. That's why `statusline.py` is pure
standard library: no imports to resolve, no interpreter startup tax beyond
python3 itself.

## Lag

Since the snapshot only refreshes after an API response, the `5h` and `7d`
percentages can lag a live session by a turn or two.

## Stale vs absent vs fabricated

A rate-limit window the reply omits (5h or weekly) is shown as absent, not a
fabricated 0%. A window whose reset timestamp has already passed is marked
stale rather than counting down to a deadline that's already wrong. Claude
Code sometimes ships a stale window with a past reset instead of dropping it;
Bifrost shows the percentage with no countdown in that case.

## ctx: raw occupancy, not Codex's native percentage

The `ctx` gauge is raw last-turn occupancy:
`last_token_usage.total_tokens / model_context_window`.

This is not Codex's own native percentage. Codex's `context-used` footer field
subtracts a fixed baseline (currently a hardcoded 12,000 tokens) from the
window before computing its percentage, so the two disagree, by a margin that
shifts with how much fixed tooling a session loads. Raw occupancy avoids
pinning Bifrost to that constant, which Codex may change between releases
(tracked in [openai/codex#40756](https://github.com/openai/codex/issues/40756)).
Codex's native footer (see
[docs/codex.md](codex.md#alternative-native-codex-footer-preset)) has no
raw-occupancy field; its context fields all route through that baseline math.
