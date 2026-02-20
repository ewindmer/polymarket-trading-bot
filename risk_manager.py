# Risk management and position sizing
import time
import json
import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional
from datetime import datetime, timezone

from config import RiskConfig
from strategies import ArbitrageSignal

logger = logging.getLogger("polymarket_arb")


@dataclass
class Position:
    """Tracks an open position."""
    id: str
    signal: ArbitrageSignal
    entry_price: float
    size_usd: float
    shares: float
    entry_time: float
    current_price: float = 0.0
    highest_price: float = 0.0
    pnl_usd: float = 0.0
    pnl_pct: float = 0.0
    status: str = "OPEN"  # OPEN, CLOSED, STOPPED_OUT, TAKE_PROFIT
    exit_price: float = 0.0
    exit_time: float = 0.0

    def update(self, current_price: float):
        """Update position with current market price."""
        self.current_price = current_price
        if current_price > self.highest_price:
            self.highest_price = current_price
        self.pnl_usd = (current_price - self.entry_price) * self.shares
        if self.entry_price > 0:
            self.pnl_pct = ((current_price - self.entry_price) / self.entry_price) * 100

    def to_dict(self) -> Dict:
        return {
            "id": self.id,
            "strategy": self.signal.strategy,
            "asset": self.signal.asset,
            "timeframe": self.signal.timeframe,
            "direction": self.signal.direction,
            "entry_price": self.entry_price,
            "current_price": self.current_price,
            "size_usd": self.size_usd,
            "shares": self.shares,
            "pnl_usd": self.pnl_usd,
            "pnl_pct": self.pnl_pct,
            "status": self.status,
            "entry_time": self.entry_time,
            "exit_time": self.exit_time,
        }


class RiskManager:
    """Risk management and position sizing."""

    def __init__(self, config: Optional[RiskConfig] = None):
        self.config = config or RiskConfig()
        self.positions: Dict[str, Position] = {}
        self.closed_positions: List[Position] = []
        self.daily_pnl: float = 0.0
        self.daily_trades: int = 0
        self.consecutive_losses: int = 0
        self.last_loss_time: float = 0
        self.portfolio_balance: float = self.config.initial_capital_usd
        self.session_start: float = time.time()
        self._strategy_exposure: Dict[str, float] = {}

    # Pre-trade checks
    def can_trade(self, signal: ArbitrageSignal) -> tuple[bool, str]:
        """Run pre-trade risk checks. Returns (allowed, reason)."""
        # 1. Daily loss cap
        if self.daily_pnl <= -self.config.max_daily_loss_usd:
            return False, f"Daily loss cap hit: ${self.daily_pnl:.2f}"

        # 2. Max open positions
        open_count = sum(1 for p in self.positions.values() if p.status == "OPEN")
        if open_count >= self.config.max_open_positions:
            return False, f"Max open positions ({self.config.max_open_positions}) reached"

        # 3. Min balance
        if self.portfolio_balance < self.config.min_portfolio_balance_usd:
            return False, f"Balance too low: ${self.portfolio_balance:.2f}"

        # 4. Consecutive loss cooldown
        if self.consecutive_losses >= self.config.max_consecutive_losses:
            cooldown_elapsed = time.time() - self.last_loss_time
            if cooldown_elapsed < self.config.cooldown_after_loss_sec:
                remaining = self.config.cooldown_after_loss_sec - cooldown_elapsed
                return False, f"Cooldown active: {remaining:.0f}s remaining after {self.consecutive_losses} consecutive losses"

        # 5. Max portfolio exposure
        total_exposure = sum(
            p.size_usd for p in self.positions.values() if p.status == "OPEN"
        )
        exposure_pct = (total_exposure / self.portfolio_balance) * 100 if self.portfolio_balance > 0 else 100
        if exposure_pct >= self.config.max_portfolio_exposure_pct:
            return False, f"Max portfolio exposure ({exposure_pct:.1f}%) exceeded"

        # 6. Duplicate market check
        for p in self.positions.values():
            if p.status == "OPEN" and p.signal.market_id == signal.market_id:
                return False, f"Already have open position in market {signal.market_id[:20]}"

        return True, "OK"

    # Position sizing
    def calculate_position_size(self, signal: ArbitrageSignal) -> float:
        """Calculate position size using fractional Kelly Criterion."""
        # Kelly inputs
        p = signal.confidence
        q = 1 - p
        b = signal.expected_profit_pct / 100 if signal.expected_profit_pct > 0 else 0.01

        # Kelly fraction
        if b <= 0:
            kelly = 0
        else:
            kelly = (b * p - q) / b

        # Fractional Kelly (25% for safety)
        kelly = max(0, kelly * 0.25)

        # Apply constraints
        max_from_portfolio = self.portfolio_balance * (self.config.max_single_trade_pct / 100)
        kelly_size = self.portfolio_balance * kelly

        # Strategy-specific max
        strategy_limits = {
            "intra_market": 500,
            "combinatorial": 300,
            "endgame": 2000,
            "cross_platform": 1000,
            "momentum_mean_reversion": 400,
        }
        strategy_max = strategy_limits.get(signal.strategy, 300)

        # Final size = min of all constraints
        size = min(kelly_size, max_from_portfolio, strategy_max)
        size = max(size, 1.0)  # Minimum $1

        logger.info(
            f"Position sizing: Kelly={kelly:.4f}, "
            f"size=${size:.2f} (max_portfolio=${max_from_portfolio:.2f}, "
            f"strategy_max=${strategy_max})"
        )

        return round(size, 2)

    # Position management
    def open_position(self, signal: ArbitrageSignal, actual_entry_price: float) -> Optional[Position]:
        """Record a new open position."""
        can, reason = self.can_trade(signal)
        if not can:
            logger.warning(f"Trade rejected: {reason}")
            return None

        size_usd = self.calculate_position_size(signal)
        shares = size_usd / actual_entry_price if actual_entry_price > 0 else 0

        position = Position(
            id=f"pos_{int(time.time()*1000)}_{signal.asset}_{signal.strategy[:5]}",
            signal=signal,
            entry_price=actual_entry_price,
            size_usd=size_usd,
            shares=shares,
            entry_time=time.time(),
            current_price=actual_entry_price,
            highest_price=actual_entry_price,
        )

        self.positions[position.id] = position
        self.portfolio_balance -= size_usd
        self.daily_trades += 1

        # Update strategy exposure
        strat = signal.strategy
        self._strategy_exposure[strat] = self._strategy_exposure.get(strat, 0) + size_usd

        logger.info(
            f"POSITION OPENED: {position.id} | {signal.strategy} | {signal.asset} "
            f"| {signal.direction} | ${size_usd:.2f} @ {actual_entry_price:.4f} "
            f"| {shares:.2f} shares"
        )

        return position

    def close_position(self, position_id: str, exit_price: float, reason: str = "") -> Optional[Position]:
        """Close a position and record PnL."""
        position = self.positions.get(position_id)
        if not position or position.status != "OPEN":
            return None

        position.exit_price = exit_price
        position.exit_time = time.time()
        position.update(exit_price)

        pnl = position.pnl_usd
        position.status = reason or "CLOSED"

        # Update daily PnL
        self.daily_pnl += pnl
        self.portfolio_balance += position.size_usd + pnl

        # Track consecutive losses
        if pnl < 0:
            self.consecutive_losses += 1
            self.last_loss_time = time.time()
        else:
            self.consecutive_losses = 0

        # Update strategy exposure
        strat = position.signal.strategy
        self._strategy_exposure[strat] = max(
            0, self._strategy_exposure.get(strat, 0) - position.size_usd
        )

        self.closed_positions.append(position)
        del self.positions[position_id]

        logger.info(
            f"POSITION CLOSED: {position_id} | PnL=${pnl:.2f} ({position.pnl_pct:.2f}%) "
            f"| Reason={position.status} | Daily PnL=${self.daily_pnl:.2f}"
        )

        return position

    # Monitoring
    def check_stops(self, market_prices: Dict[str, float]) -> List[str]:
        """Check positions for stop-loss and take-profit triggers."""
        to_close = []

        for pos_id, pos in self.positions.items():
            if pos.status != "OPEN":
                continue

            market_id = pos.signal.market_id
            current_price = market_prices.get(market_id)
            if current_price is None:
                continue

            pos.update(current_price)

            # Get stop/TP levels from signal metadata
            stop_loss_pct = pos.signal.metadata.get("stop_loss_pct", 3.0)
            take_profit_pct = pos.signal.metadata.get("take_profit_pct", 5.0)

            # Stop Loss
            loss_pct = ((pos.entry_price - current_price) / pos.entry_price) * 100
            if loss_pct > stop_loss_pct:
                to_close.append((pos_id, current_price, "STOP_LOSS"))
                continue

            # Take Profit
            gain_pct = ((current_price - pos.entry_price) / pos.entry_price) * 100
            if gain_pct > take_profit_pct:
                to_close.append((pos_id, current_price, "TAKE_PROFIT"))
                continue

            # Trailing Stop (for profitable positions)
            if current_price > pos.entry_price and pos.highest_price > 0:
                trail_drop = ((pos.highest_price - current_price) / pos.highest_price) * 100
                if trail_drop > self.config.trailing_stop_pct:
                    to_close.append((pos_id, current_price, "TRAILING_STOP"))
                    continue

            # For arbitrage strategies, check resolution
            if pos.signal.strategy in ("intra_market", "combinatorial", "endgame"):
                if current_price >= 0.99:  # Near resolution payout
                    to_close.append((pos_id, current_price, "RESOLUTION"))

        return to_close

    # Reporting
    def get_status(self) -> Dict[str, Any]:
        """Get current risk manager status."""
        open_positions = [p for p in self.positions.values() if p.status == "OPEN"]
        total_exposure = sum(p.size_usd for p in open_positions)
        unrealized_pnl = sum(p.pnl_usd for p in open_positions)

        return {
            "portfolio_balance": round(self.portfolio_balance, 2),
            "daily_pnl": round(self.daily_pnl, 2),
            "unrealized_pnl": round(unrealized_pnl, 2),
            "total_pnl": round(self.daily_pnl + unrealized_pnl, 2),
            "open_positions": len(open_positions),
            "total_exposure_usd": round(total_exposure, 2),
            "exposure_pct": round((total_exposure / max(self.portfolio_balance, 1)) * 100, 1),
            "daily_trades": self.daily_trades,
            "consecutive_losses": self.consecutive_losses,
            "closed_trades_today": len(self.closed_positions),
            "win_rate": self._calculate_win_rate(),
            "strategy_exposure": dict(self._strategy_exposure),
        }

    def _calculate_win_rate(self) -> float:
        if not self.closed_positions:
            return 0.0
        wins = sum(1 for p in self.closed_positions if p.pnl_usd > 0)
        return round((wins / len(self.closed_positions)) * 100, 1)

    def get_performance_summary(self) -> str:
        """Generate a human-readable performance summary."""
        status = self.get_status()
        lines = [
            "PORTFOLIO STATUS",
            f"Balance: ${status['portfolio_balance']:.2f}",
            f"Daily PnL: ${status['daily_pnl']:.2f}",
            f"Unrealized: ${status['unrealized_pnl']:.2f}",
            f"Total PnL: ${status['total_pnl']:.2f}",
            f"Open Positions: {status['open_positions']}",
            f"Exposure: ${status['total_exposure_usd']:.2f} ({status['exposure_pct']}%)",
            f"Trades Today: {status['daily_trades']}",
            f"Win Rate: {status['win_rate']:.1f}%",
            f"Consecutive Losses: {status['consecutive_losses']}",
        ]
        return "\n".join(lines)

    def save_trades(self, filepath: str = "trades.json"):
        """Save all trade history to JSON."""
        trades = [p.to_dict() for p in self.closed_positions]
        with open(filepath, "w") as f:
            json.dump(trades, f, indent=2)

    def reset_daily(self):
        """Reset daily counters (call at start of new day)."""
        self.daily_pnl = 0.0
        self.daily_trades = 0
        self.closed_positions.clear()
        self.consecutive_losses = 0
        logger.info("Daily risk counters reset")
