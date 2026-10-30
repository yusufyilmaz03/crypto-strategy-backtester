# Telegram commands and mobile access (plan written 2026-10-30)

Branch: `phase-4e-telegram`.

Two goals:

1. Ask the Telegram bot about an instance (positions, signals, trades) and, when the
   instance has manual close, close a position with a button.
2. Use the full dashboard from a phone.

Status: part A is implemented (`app/telegram_commands.py`, `app/manual_close.py`).
Part B is setup on the Mac and needs no code.

## Decisions

- Telegram gets commands and a close button (part A).
- Mobile access to the dashboard goes through **Tailscale** (part B).
- **No Telegram Mini App.** A Mini App still needs an HTTPS URL the phone can reach,
  so it doesn't solve access; it would only wrap the dashboard inside Telegram. With
  Tailscale working there is nothing left for it to add.
- The bot stays on the Mac (launchd service) for a long time. Docker on a server comes
  later, so part B is written for the Mac.

## A. Telegram commands

### How it receives messages

`app/notify.py` only sends. `app/telegram_commands.py` adds a background thread in the
supervisor (`run_all.py`) that long-polls `getUpdates`:

- The connection is outgoing. Nothing is exposed.
- No webhook. Only one program may read a bot's updates at a time (Telegram answers
  "Conflict" to the second one). The supervisor lock already allows one supervisor;
  `telegram-setup` also reads updates, so run it with the supervisor stopped. A
  conflict is logged once and retried every 30 s.
- The offset is kept in memory only. After a restart Telegram hands back the updates
  that weren't confirmed; commands older than `MAX_AGE_S` (2 min) are ignored, so
  nothing sent while the supervisor was down gets answered late.
- An error in the command thread never stops the supervisor, the same rule the
  Watcher follows.

### Access control

The bot answers only messages and button presses from the chat in
`TELEGRAM_CHAT_ID`. Everything else gets no answer. In a private chat the chat id is
your user id. Don't add the bot to a group: everyone in it could press the buttons.

### Commands

Data comes from `app/reporting.py`, the same functions the dashboard uses. No new
queries.

| Command | Reply |
|---|---|
| `/status` | Every instance: process state, return vs B&H, open positions; kill switch state |
| `/instance` | One button per instance; tapping one opens its view |
| `/instance NAME` | Opens one instance directly |
| instance view | Setup, process state, return vs B&H, max DD, days, open/closed, win rate, realized PnL; buttons Positions / Signals / Trades / Refresh |
| positions | Entry price and time, last price, change vs entry (before fees), stop; Close buttons |
| signals | Last 10 signals and what the engine did (testnet: last 10 events) |
| trades | Last 10 closed trades (testnet: last 10 orders) |
| `/help` | The list above |

Navigation edits the same message, so the chat doesn't fill up. The command list is
registered with `setMyCommands`, so it shows in Telegram's menu. `/kill` was left out
for now; it can be added later if switching the kill switch from the phone is wanted.

### Close button

The panel and Telegram file close requests through one function,
`manual_close.request()` (instance has manual close, is running, has the position, no
request waiting). The instance's own process sells (`trading/position_watch.py`,
`ManualClose`) and the Watcher reports the closed trade with reason MANUAL.

1. The positions view has a Close button per position when the instance has
   `manual_close`. A position with a request waiting shows "close requested" instead.
2. Close asks first: "Close BTC/USDT on NAME at the market now? [Yes, close] [Cancel]".
3. Yes files the request. It is refused when pressed more than `CONFIRM_TTL_S`
   (2 min) after the question.
4. Close buttons carry a short id that lives in the supervisor's memory. After a
   restart old buttons answer "expired", so an old message can't close anything.

`manual_close` exists for paper instances only, so the button never sends a real
order. Extending it to testnet or live is separate work and needs explicit approval
first (CLAUDE.md rule on code that sends orders).

### Tests

`tests/test_telegram_commands.py` replaces `telegram_call` with a fake: access control,
old commands, every view, the confirm step, late and expired confirmations, a refused
close, "message is not modified" on Refresh, offsets and the conflict log. The panel's
close endpoint test (`tests/test_manual_close.py`) covers the shared function.

### Trying it

Restart the supervisor (`python -m kripto_bot service restart`, or stop and start
`python -m kripto_bot run`). It prints "Telegram notifications and commands on". Then
send `/status` to the bot.

## B. Dashboard on the phone with Tailscale (Mac)

The panel stays on 127.0.0.1. `tailscale serve` puts an HTTPS address in front of it
that only your own Tailscale devices can reach. No code change, and the panel is not
reachable from the home network or the internet.

1. Install Tailscale on the Mac (tailscale.com/download, or the Mac App Store) and on
   the phone. Log in with the same account on both.
2. Set `DASHBOARD_PASSWORD` in `.env` and restart the supervisor. The panel only
   requires it when it listens beyond 127.0.0.1, but behind `tailscale serve` the
   password is the second lock if a device in the tailnet is lost.
3. On the Mac:
   `tailscale serve --bg 5002`
   (with the App Store app the command is
   `/Applications/Tailscale.app/Contents/MacOS/Tailscale serve --bg 5002`).
   It prints the address, `https://<mac-name>.<tailnet>.ts.net`. HTTPS certificates
   must be enabled once in the Tailscale admin console (DNS → HTTPS certificates);
   the command says so if they aren't. `--bg` keeps it running across restarts;
   `tailscale serve reset` removes it.
4. On the phone, with Tailscale on: open that address and log in (any user name, the
   password from step 2). Add it to the home screen for one-tap access.

Do not use `tailscale funnel`: it makes the address public.

Why Tailscale and not a public tunnel (Cloudflare Tunnel, ngrok): the panel can close
positions and change settings. A public URL protected only by basic auth is too thin,
especially once live trading comes. The cost is that the Tailscale app must be on
when using the panel.

The other way is `DASHBOARD_HOST=0.0.0.0` in `.env`. It works over Tailscale too, but
it also opens the panel to the home network over plain HTTP, so `serve` is better.

### To check

- How the dashboard looks on a phone screen. The Overview cards were checked at
  375–1400 px (headless Chrome, 2026-10-30); the instance page tables, charts,
  Playground and Settings were not. Small CSS fixes may be needed.
- The panel and the bot are only reachable while the Mac is awake. `--keep-awake`
  prevents idle sleep on power, not sleep with the lid closed.
