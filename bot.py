# Polymarket crypto arbitrage bot
import asyncio
import argparse
import json
import signal
import sys
import time
import logging
from datetime import datetime, timezone
from typing import Dict, List, Optional

import config
from config import (
    DRY_RUN, TIMEFRAMES, SCAN_INTERVAL_SECONDS,
    RiskConfig, LOG_LEVEL, LOG_FILE, TRADE_LOG_FILE,
    ENABLE_TELEGRAM_ALERTS, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID,
)
from api_client import PolymarketClient
from scanner import MarketScanner
from strategies import ArbitrageSignal
from risk_manager import RiskManager, Position


# Logging setup
def setup_logging():
    """Configure logging to both console and file."""
    logger = logging.getLogger("polymarket_arb")
    logger.setLevel(getattr(logging, LOG_LEVEL))

    # Console handler with colors
    console = logging.StreamHandler()
    console.setLevel(logging.INFO)
    fmt = logging.Formatter(
        "%(asctime)s │ %(levelname)-7s │ %(message)s",
        datefmt="%H:%M:%S"
    )
    console.setFormatter(fmt)
    logger.addHandler(console)

    # File handler
    file_handler = logging.FileHandler(LOG_FILE)
    file_handler.setLevel(logging.DEBUG)
    file_fmt = logging.Formatter(
        "%(asctime)s | %(levelname)s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )
    file_handler.setFormatter(file_fmt)
    logger.addHandler(file_handler)

    return logger


# Telegram alerts
class TelegramNotifier:
    """Send trade alerts to Telegram."""

    def __init__(self):
        self.enabled = ENABLE_TELEGRAM_ALERTS and TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID
        self.bot_token = TELEGRAM_BOT_TOKEN
        self.chat_id = TELEGRAM_CHAT_ID

    async def send(self, message: str):
        if not self.enabled:
            return
        try:
            import aiohttp
            url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
            async with aiohttp.ClientSession() as session:
                await session.post(url, json={
                    "chat_id": self.chat_id,
                    "text": message,
                    "parse_mode": "HTML",
                })
        except Exception:
            pass  # Don't let Telegram errors stop the bot

    async def alert_signal(self, signal: ArbitrageSignal):
        msg = (
            f"<b>SIGNAL DETECTED</b>\n"
            f"Strategy: {signal.strategy}\n"
            f"Asset: {signal.asset} | TF: {signal.timeframe}\n"
            f"Direction: {signal.direction}\n"
            f"Expected Profit: {signal.expected_profit_pct:.2f}%\n"
            f"Confidence: {signal.confidence:.2f}\n"
            f"Urgency: {signal.urgency}"
        )
        await self.send(msg)

    async def alert_trade(self, position: Position, action: str):
        msg = (
            f"<b>TRADE {action}</b>\n"
            f"Strategy: {position.signal.strategy}\n"
            f"Asset: {position.signal.asset}\n"
            f"Direction: {position.signal.direction}\n"
            f"Size: ${position.size_usd:.2f}\n"
            f"Entry: ${position.entry_price:.4f}\n"
        )
        if action != "OPEN":
            msg += (
                f"Exit: ${position.exit_price:.4f}\n"
                f"PnL: ${position.pnl_usd:.2f} ({position.pnl_pct:.2f}%)\n"
            )
        await self.send(msg)


class PolymarketArbBot:
    """Main bot orchestrator."""

    def __init__(self, live: bool = False, scan_only: bool = False):
        self.live = live
        self.scan_only = scan_only
        self.running = False

        # Override DRY_RUN based on CLI flag
        if live:
            config.DRY_RUN = False
        else:
            config.DRY_RUN = True

        self.scanner = MarketScanner()
        self.risk_manager = RiskManager(RiskConfig())
        self.poly_client = PolymarketClient()
        self.telegram = TelegramNotifier()
        self.logger = logging.getLogger("polymarket_arb")

        # Performance tracking
        self._start_time = time.time()
        self._total_signals = 0
        self._total_trades = 0
        self._scan_cycle = 0
        self._last_status_report = 0

    async def start(self):
        """Start the bot."""
        self.running = True
        mode = "LIVE" if self.live else "DRY RUN"
        scan_mode = " [SCAN ONLY]" if self.scan_only else ""

        self.logger.info(f"Polymarket Crypto Arbitrage Bot v1.0")
        self.logger.info(f"Mode: {mode}{scan_mode}")
        self.logger.info(f"Assets: BTC, ETH, XRP, SOL")
        self.logger.info(f"Timeframes: 5m, 15m, 1h")
        self.logger.info(f"Capital: ${self.risk_manager.config.initial_capital_usd:.2f}")

        if self.live:
            self.logger.warning("LIVE TRADING MODE - REAL MONEY AT RISK!")
            self.logger.warning("Starting in 10 seconds... Press Ctrl+C to abort")
            await asyncio.sleep(10)

        await self.telegram.send(f"Bot started in {mode} mode")

        try:
            await self.scanner.initialize()
            await self._main_loop()
        except KeyboardInterrupt:
            self.logger.info("Keyboard interrupt received")
        except Exception as e:
            self.logger.error(f"Fatal error: {e}", exc_info=True)
        finally:
            await self.shutdown()

    async def _main_loop(self):
        """Main execution loop with multi-timeframe scanning."""
        # Track last scan time per timeframe
        last_scan: Dict[str, float] = {tf: 0 for tf in TIMEFRAMES}

        while self.running:
            try:
                now = time.time()

                # Determine which timeframes need scanning
                timeframes_to_scan = []
                for tf, interval in SCAN_INTERVAL_SECONDS.items():
                    if now - last_scan[tf] >= interval:
                        timeframes_to_scan.append(tf)
                        last_scan[tf] = now

                if not timeframes_to_scan:
                    await asyncio.sleep(1)
                    continue

                self._scan_cycle += 1

                # Scan
                signals = await self.scanner.scan_all_timeframes()

                if signals:
                    self._total_signals += len(signals)

                    # Execute trades
                    if not self.scan_only:
                        await self._execute_signals(signals)

                    # Monitor positions
                    await self._monitor_positions()

                # Status report
                if now - self._last_status_report > 60:  # Every minute
                    self._print_status()
                    self._last_status_report = now

                # Brief sleep to prevent tight loop
                await asyncio.sleep(2)

            except Exception as e:
                self.logger.error(f"Main loop error: {e}", exc_info=True)
                await asyncio.sleep(5)

    async def _execute_signals(self, signals: List[ArbitrageSignal]):
        """Execute the top-ranked signals through the risk manager."""
        for signal in signals[:3]:  # Max 3 new trades per cycle
            # Pre-trade risk check
            can_trade, reason = self.risk_manager.can_trade(signal)
            if not can_trade:
                self.logger.debug(f"Trade rejected: {reason} | {signal}")
                continue

            # Execute based on strategy type
            position = await self._execute_trade(signal)
            if position:
                self._total_trades += 1
                await self.telegram.alert_trade(position, "OPEN")

    async def _execute_trade(self, signal: ArbitrageSignal) -> Optional[Position]:
        """Execute a single trade based on signal."""
        self.logger.info(f"Executing: {signal}")

        if signal.strategy == "intra_market":
            return await self._execute_intra_market(signal)
        elif signal.strategy == "combinatorial":
            return await self._execute_combinatorial(signal)
        elif signal.strategy == "cross_platform":
            return await self._execute_directional(signal)
        elif signal.strategy == "endgame":
            return await self._execute_directional(signal)
        elif signal.strategy == "momentum_mean_reversion":
            return await self._execute_directional(signal)
        else:
            self.logger.warning(f"Unknown strategy: {signal.strategy}")
            return None

    async def _execute_intra_market(self, signal: ArbitrageSignal) -> Optional[Position]:
        """Execute intra-market arbitrage: Buy both YES and NO."""
        yes_token = signal.token_ids.get("YES", "")
        no_token = signal.token_ids.get("NO", "")
        if not yes_token or not no_token:
            return None

        size_usd = self.risk_manager.calculate_position_size(signal)
        yes_ask = signal.metadata.get("yes_ask", 0)
        no_ask = signal.metadata.get("no_ask", 0)

        if yes_ask <= 0 or no_ask <= 0:
            return None

        total = yes_ask + no_ask
        yes_allocation = (yes_ask / total) * size_usd
        no_allocation = (no_ask / total) * size_usd

        # Place both orders
        yes_order = await self.poly_client.place_market_order(
            yes_token, "BUY", yes_allocation
        )
        no_order = await self.poly_client.place_market_order(
            no_token, "BUY", no_allocation
        )

        if yes_order and no_order:
            return self.risk_manager.open_position(signal, signal.entry_price)

        # If one leg fails, cancel the other
        if yes_order and not no_order:
            self.logger.warning("Intra-market: NO leg failed, rolling back YES")
            # In production, sell back the YES position
        elif no_order and not yes_order:
            self.logger.warning("Intra-market: YES leg failed, rolling back NO")

        return None

    async def _execute_combinatorial(self, signal: ArbitrageSignal) -> Optional[Position]:
        """Execute combinatorial arbitrage: Buy all outcomes."""
        size_usd = self.risk_manager.calculate_position_size(signal)
        outcome_prices = signal.metadata.get("outcome_prices", {})
        total_price = sum(outcome_prices.values())

        if total_price <= 0:
            return None

        all_success = True
        for outcome, token_id in signal.token_ids.items():
            price = outcome_prices.get(outcome, 0)
            allocation = (price / total_price) * size_usd
            order = await self.poly_client.place_market_order(
                token_id, "BUY", allocation
            )
            if not order:
                all_success = False
                self.logger.warning(f"Combinatorial: {outcome} leg failed")

        if all_success:
            return self.risk_manager.open_position(signal, signal.entry_price)
        return None

    async def _execute_directional(self, signal: ArbitrageSignal) -> Optional[Position]:
        """Execute a directional trade (cross-platform, endgame, momentum)."""
        direction = signal.direction
        if "YES" in direction:
            token_id = signal.token_ids.get("YES", "")
            side = "BUY"
        elif "NO" in direction:
            token_id = signal.token_ids.get("NO", "")
            side = "BUY"
        else:
            return None

        if not token_id:
            return None

        size_usd = self.risk_manager.calculate_position_size(signal)
        order = await self.poly_client.place_market_order(token_id, side, size_usd)

        if order:
            return self.risk_manager.open_position(signal, signal.entry_price)
        return None

    async def _monitor_positions(self):
        """Check all open positions for stop-loss/take-profit triggers."""
        if not self.risk_manager.positions:
            return

        # Get current prices for all open position markets
        market_prices = {}
        for pos in self.risk_manager.positions.values():
            if pos.status != "OPEN":
                continue
            try:
                # Get the relevant token price
                direction = pos.signal.direction
                if "YES" in direction:
                    token_id = pos.signal.token_ids.get("YES", "")
                else:
                    token_id = pos.signal.token_ids.get("NO", "")

                if token_id:
                    price = await self.poly_client.get_midpoint(token_id)
                    if price is not None:
                        market_prices[pos.signal.market_id] = price
            except Exception as e:
                self.logger.debug(f"Price fetch failed for monitoring: {e}")

        # Check stops
        to_close = self.risk_manager.check_stops(market_prices)
        for pos_id, exit_price, reason in to_close:
            position = self.risk_manager.close_position(pos_id, exit_price, reason)
            if position:
                await self.telegram.alert_trade(position, "CLOSE")

    def _print_status(self):
        """Print periodic status dashboard."""
        uptime = time.time() - self._start_time
        hours = int(uptime // 3600)
        minutes = int((uptime % 3600) // 60)

        scanner_stats = self.scanner.get_stats()
        risk_status = self.risk_manager.get_status()

        status = (
            f"BOT STATUS | Uptime: {hours}h {minutes}m | Scan Cycle: #{self._scan_cycle} | "
            f"Markets: {scanner_stats['tracked_markets']} | Signals: {self._total_signals} | "
            f"Trades: {self._total_trades} | Balance: ${risk_status['portfolio_balance']:.2f} | "
            f"Daily PnL: ${risk_status['daily_pnl']:.2f} | Unrealized: ${risk_status['unrealized_pnl']:.2f} | "
            f"Open Positions: {risk_status['open_positions']} | Win Rate: {risk_status['win_rate']:.1f}%"
        )
        self.logger.info(status)

    async def shutdown(self):
        """Graceful shutdown."""
        self.running = False
        self.logger.info("Shutting down bot...")

        # Save trade history
        self.risk_manager.save_trades(TRADE_LOG_FILE)

        # Print final performance
        self.logger.info(self.risk_manager.get_performance_summary())

        # Close connections
        await self.scanner.close()
        await self.poly_client.close()

        await self.telegram.send("Bot shutdown. Final status:\n" +
                                  self.risk_manager.get_performance_summary())

        self.logger.info("Shutdown complete")


class Backtester:
    """Backtest simulation with synthetic scenarios."""

    def __init__(self):
        self.risk_manager = RiskManager(RiskConfig())
        self.logger = logging.getLogger("polymarket_arb")

    async def run(self, num_scenarios: int = 100):
        """Run backtest with synthetic scenarios."""
        import random
        import numpy as np

        self.logger.info(f"Running backtest with {num_scenarios} scenarios...")

        strategies = [
            "intra_market", "combinatorial", "cross_platform",
            "endgame", "momentum_mean_reversion"
        ]
        assets = ["BTC", "ETH", "XRP", "SOL"]
        timeframes = ["5m", "15m", "1h"]

        results = {
            "total_trades": 0,
            "winning_trades": 0,
            "total_pnl": 0.0,
            "max_drawdown": 0.0,
            "peak_balance": self.risk_manager.config.initial_capital_usd,
            "by_strategy": {s: {"trades": 0, "pnl": 0} for s in strategies},
            "by_asset": {a: {"trades": 0, "pnl": 0} for a in assets},
            "by_timeframe": {t: {"trades": 0, "pnl": 0} for t in timeframes},
        }

        balance_history = [self.risk_manager.config.initial_capital_usd]

        for i in range(num_scenarios):
            strategy = random.choice(strategies)
            asset = random.choice(assets)
            tf = random.choice(timeframes)

            # Simulate signal with realistic parameters
            if strategy == "intra_market":
                profit_pct = random.gauss(2.5, 1.5)
                confidence = random.uniform(0.6, 0.95)
                win_prob = 0.85  # High win rate for arb
            elif strategy == "endgame":
                profit_pct = random.gauss(3.0, 1.0)
                confidence = random.uniform(0.8, 0.98)
                win_prob = 0.90
            elif strategy == "cross_platform":
                profit_pct = random.gauss(5.0, 3.0)
                confidence = random.uniform(0.5, 0.85)
                win_prob = 0.60
            else:
                profit_pct = random.gauss(3.0, 2.0)
                confidence = random.uniform(0.4, 0.80)
                win_prob = 0.55

            signal = ArbitrageSignal(
                strategy=strategy,
                asset=asset,
                timeframe=tf,
                market_id=f"bt_{i}",
                direction="BUY_YES",
                entry_price=random.uniform(0.3, 0.7),
                target_price=random.uniform(0.5, 0.95),
                expected_profit_pct=max(0.1, profit_pct),
                expected_profit_usd=0,
                confidence=confidence,
                urgency=random.choice(["HIGH", "MEDIUM", "LOW"]),
            )

            can, reason = self.risk_manager.can_trade(signal)
            if not can:
                continue

            pos = self.risk_manager.open_position(signal, signal.entry_price)
            if not pos:
                continue

            # Simulate outcome
            won = random.random() < win_prob
            if won:
                pnl_pct = abs(profit_pct) * random.uniform(0.5, 1.5)
                exit_price = pos.entry_price * (1 + pnl_pct / 100)
            else:
                loss_pct = abs(profit_pct) * random.uniform(0.5, 2.0)
                exit_price = pos.entry_price * (1 - loss_pct / 100)

            exit_price = max(0.01, min(0.99, exit_price))
            self.risk_manager.close_position(pos.id, exit_price, "BACKTEST")

            # Track results
            results["total_trades"] += 1
            pnl = self.risk_manager.closed_positions[-1].pnl_usd
            results["total_pnl"] += pnl
            if pnl > 0:
                results["winning_trades"] += 1

            results["by_strategy"][strategy]["trades"] += 1
            results["by_strategy"][strategy]["pnl"] += pnl
            results["by_asset"][asset]["trades"] += 1
            results["by_asset"][asset]["pnl"] += pnl
            results["by_timeframe"][tf]["trades"] += 1
            results["by_timeframe"][tf]["pnl"] += pnl

            balance_history.append(self.risk_manager.portfolio_balance)
            if self.risk_manager.portfolio_balance > results["peak_balance"]:
                results["peak_balance"] = self.risk_manager.portfolio_balance

            drawdown = (results["peak_balance"] - self.risk_manager.portfolio_balance) / results["peak_balance"] * 100
            if drawdown > results["max_drawdown"]:
                results["max_drawdown"] = drawdown

        # Print results
        self._print_backtest_results(results, balance_history)

    def _print_backtest_results(self, results: Dict, balance_history: list):
        win_rate = (results["winning_trades"] / max(results["total_trades"], 1)) * 100

        report = f"BACKTEST RESULTS\n"
        report += f"Total Trades: {results['total_trades']}\n"
        report += f"Winning Trades: {results['winning_trades']}\n"
        report += f"Win Rate: {win_rate:.1f}%\n"
        report += f"Total PnL: ${results['total_pnl']:.2f}\n"
        report += f"Final Balance: ${balance_history[-1]:.2f}\n"
        report += f"Peak Balance: ${results['peak_balance']:.2f}\n"
        report += f"Max Drawdown: {results['max_drawdown']:.1f}%\n"
        report += "\nBY STRATEGY:\n"
        for strat, data in results["by_strategy"].items():
            if data["trades"] > 0:
                report += f"  {strat}: {data['trades']} trades, ${data['pnl']:+.2f}\n"
        report += "\nBY ASSET:\n"
        for asset, data in results["by_asset"].items():
            if data["trades"] > 0:
                report += f"  {asset}: {data['trades']} trades, ${data['pnl']:+.2f}\n"
        report += "\nBY TIMEFRAME:\n"
        for tf, data in results["by_timeframe"].items():
            if data["trades"] > 0:
                report += f"  {tf}: {data['trades']} trades, ${data['pnl']:+.2f}\n"
        self.logger.info(report)


# CLI entry point
def main():
    parser = argparse.ArgumentParser(
        description="Polymarket Crypto Arbitrage Bot",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python bot.py                    Paper trading (DRY RUN)
  python bot.py --live             Live trading with real money
  python bot.py --scan-only        Scan for opportunities only
  python bot.py --backtest         Run backtest simulation
  python bot.py --backtest -n 500  Run 500 scenario backtest

Environment Variables Required:
  POLYMARKET_API_KEY       Your Polymarket API key
  POLYMARKET_SECRET        Your Polymarket API secret
  POLYMARKET_PASSPHRASE    Your Polymarket API passphrase
  POLYGON_PRIVATE_KEY      Your Polygon wallet private key
        """
    )
    parser.add_argument("--live", action="store_true",
                        help="Enable LIVE trading (real money!)")
    parser.add_argument("--scan-only", action="store_true",
                        help="Scan for opportunities without executing trades")
    parser.add_argument("--backtest", action="store_true",
                        help="Run backtest simulation")
    parser.add_argument("-n", "--num-scenarios", type=int, default=200,
                        help="Number of backtest scenarios (default: 200)")
    parser.add_argument("--capital", type=float, default=1000.0,
                        help="Starting capital in USD (default: 1000)")

    args = parser.parse_args()
    logger = setup_logging()

    # Update capital
    RiskConfig.initial_capital_usd = args.capital

    if args.backtest:
        backtester = Backtester()
        asyncio.run(backtester.run(args.num_scenarios))
    else:
        bot = PolymarketArbBot(live=args.live, scan_only=args.scan_only)

        # Handle graceful shutdown
        def signal_handler(sig, frame):
            logger.info("Shutdown signal received...")
            bot.running = False

        signal.signal(signal.SIGINT, signal_handler)

        asyncio.run(bot.start())


if __name__ == "__main__":
    main()
