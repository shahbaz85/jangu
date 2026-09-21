# MR-70 V3 paper-study bot

Sends Telegram alerts when V3 fires on ETH, BNB, SOL or DOGE (15m), tracks what
the rule would have done, and writes it all to a CSV you copy into the sheet.

**It sends signals. It cannot trade.** There is no order-placing code, no API key
is used, and a test scans the source and fails if any order method ever appears.
Use a read-only setup: the bot only needs public market data, so give it no keys
at all.

**V3 is not expected to make money.** It tested at 72.9% wins against a ~75%
break-even. You are measuring two things the backtest cannot: how real fills
compare with the touch-fill assumption, and whether your own take/skip judgement
beats the rule.

## One-time setup

Set the Telegram variables permanently (PowerShell, then reopen the window):

```powershell
[Environment]::SetEnvironmentVariable("TELEGRAM_BOT_TOKEN", "123456:AA...", "User")
[Environment]::SetEnvironmentVariable("TELEGRAM_CHAT_ID",   "987654321",   "User")
```

Token comes from @BotFather, chat id from @userinfobot. Check it works:

```powershell
.\run_bot.ps1 -TestMessage
```

A message should arrive within a few seconds. If nothing comes, the variables are
not set in this window — reopen PowerShell and try again.

## Running

```powershell
.\run_bot.ps1
```

It wakes 20 seconds after each 15m close, checks all four coins, and restarts
itself if the process dies. Leave the window open. `Ctrl+C` stops it.

**Start automatically at login** — Task Scheduler → Create Task:

- General: *Run only when user is logged on*, tick *Run with highest privileges*
- Triggers: *At log on*
- Actions: Start a program
  - Program: `powershell.exe`
  - Arguments: `-ExecutionPolicy Bypass -File "C:\path\to\run_bot.ps1"`
  - Start in: the folder containing `run_bot.ps1`
- Settings: untick *Stop the task if it runs longer than...*

## Checking it is alive

- A **heartbeat** arrives daily after 06:00 UTC: "Bot alive — N signals in last 24h".
  Silence after that means it is not running, not that the market was quiet.
- A **weekly summary** arrives Sunday after 18:00 UTC.
- `logs\study_bot_<date>.log` shows every cycle.

## What you get

`mr70\study_signals.csv`, in the same column order as the sheet's Signal Log.
The bot fills its columns; yours stay empty for you to complete:

| bot fills | you fill |
|---|---|
| `#`, date, time, symbol, side | `taken`, `reason_skipped` |
| `signal_entry_price`, `bot_stop`, `bot_target` | `my_exit_price`, `my_exit_time`, `my_exit_reason` |
| `paper_fill_traded_through`, `touch_fill` | |
| `bot_rule_exit_price`, `bot_exit_reason`, `rule_R` | |

`bot_exit_reason` is `TP`, `SL`, `TIME`, `UNFILLED`, or `MISSED` when the bot was
offline and the signal was already more than two bars old — those are logged but
never alerted, so you are not told to enter a trade whose moment has passed.

`touch_fill` is whether price reached the entry; `paper_fill_traded_through` is
whether it went 0.05 ATR beyond. The second is the realistic one, and the gap
between them is a large part of what this study is for.

## Honest expectations on sample size

V3 fires roughly 50 times a month across these four coins, and you will take only
some of them. Detecting whether your judgement beats the rule by a few percentage
points needs several hundred decisions — that is years at this rate, not a month.

What a month **can** settle is the fill question: touch versus traded-through
across ~50 signals is enough to see whether the backtest's fill assumption was
optimistic, and by roughly how much. Treat that as the deliverable, and treat any
win-rate comparison as a note-to-self until the count is far higher.

## Files

- `mr70/study_bot.py` — the bot
- `mr70/study_bot_tests.py` — five tests, all must pass before use
- `mr70/study_state.json` — sent signals and open trades; survives restarts
- `mr70/study_signals.csv` — the log
