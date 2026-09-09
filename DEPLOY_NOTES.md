# Finanse bot — repaired baseline

Changes in this build:
- restored fixed SCALP_PAIRS/SWING_PAIRS for automatic scans;
- concurrent scanning with bounded concurrency (4), instead of sequential top-150 scanning;
- max 3 automatic signals per scan, ranked by confidence/score;
- duplicate suppression retained;
- automatic signals are recorded in `user_signals` for correct tracking notifications;
- closed signals now persist `profit_pct` and `closed_at`;
- duplicate Telegram callback handler registration removed; `trade_` handler is registered before generic callback handler;
- `.env` intentionally excluded from the release package.

Before production:
1. Stop the current bot.
2. Back up the production `trading_bot.db` and `.env`.
3. Copy this release over the bot source, keeping production `.env` and DB.
4. Install requirements in the existing Python environment.
5. Run `python -m compileall -q .`.
6. Start the service.
7. Confirm logs contain `Планировщик: фиксированные SCALP/SWING пары`.
8. Confirm the first `15m` scan completes and logs `[SCAN 15m]`.

Do not treat signal frequency or historical backtest results as a profit guarantee. Validate paper/small-size behavior before increasing capital.
