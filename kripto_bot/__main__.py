# kripto_bot/__main__.py
"""Command line entry point: python -m kripto_bot <command> [options]

Run from the project root. `python -m kripto_bot <command> --help` shows a
command's options.
"""
import runpy
import sys

from kripto_bot import paths

COMMANDS = {
    "run": ("kripto_bot.app.run_all", "dashboard + every enabled bot instance (day-to-day use)"),
    "dashboard": ("kripto_bot.app.dashboard", "control panel only, http://127.0.0.1:5002"),
    "paper": ("kripto_bot.trading.run_realtime", "one paper instance (--instance NAME)"),
    "testnet": ("kripto_bot.trading.run_testnet", "one testnet instance (--instance NAME)"),
    "testnet-check": ("kripto_bot.trading.testnet_check", "check the testnet connection (--yes places orders)"),
    "backtest": ("kripto_bot.analysis.multi_backtest", "grid backtest → results/multi_backtest_strategies.csv"),
    "research": ("kripto_bot.analysis.research", "walk-forward research → results/research_*.csv"),
    "forward-report": ("kripto_bot.analysis.forward_report", "forward-test metrics vs buy-and-hold"),
    "holdout-test": ("kripto_bot.analysis.holdout_test", "the one-off v9 holdout test (already run)"),
    "telegram-setup": ("kripto_bot.tools.telegram_setup", "find the Telegram chat id and send a test message"),
    "health": ("kripto_bot.tools.binance_health", "Binance ping, clock drift and candle latency"),
    "plot-summary": ("kripto_bot.tools.plot_summary", "return bar chart from the grid backtest"),
    "plot-chart": ("kripto_bot.tools.plot_multi_chart", "profitable out-of-sample combinations chart"),
}


def usage():
    width = max(map(len, COMMANDS))
    lines = ["usage: python -m kripto_bot <command> [options]", "", "commands:"]
    lines += [f"  {name:<{width}}  {text}" for name, (_, text) in COMMANDS.items()]
    return "\n".join(lines)


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if not argv or argv[0] in ("-h", "--help", "help"):
        print(usage())
        return 0
    if argv[0] not in COMMANDS:
        print(f"unknown command '{argv[0]}'\n\n{usage()}", file=sys.stderr)
        return 2
    try:
        moved, skipped = paths.migrate_legacy()
    except paths.LegacyInUse as e:
        print(f"❌ {e}", file=sys.stderr)
        return 1
    if moved:
        print(f"📦 Moved files from the old layout into runtime/ and results/: {', '.join(moved)}")
    if skipped:
        print(f"⚠️  Left in the project root because runtime/ or results/ already has them: "
              f"{', '.join(skipped)}. Compare and move them by hand.")
    module = COMMANDS[argv[0]][0]
    sys.argv = [f"kripto_bot {argv[0]}", *argv[1:]]
    runpy.run_module(module, run_name="__main__", alter_sys=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
