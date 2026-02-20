# Arbitrage strategy implementations
import time
import logging
import numpy as np
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple
from datetime import datetime, timezone, timedelta

from config import (
    IntraMarketConfig, CombinatorialConfig, CrossPlatformConfig,
    EndgameConfig, MomentumConfig, CRYPTO_ASSETS,
)

logger = logging.getLogger("polymarket_arb")


@dataclass
class ArbitrageSignal:
    """Arbitrage opportunity signal."""
    strategy: str               # "intra_market", "combinatorial", "cross_platform", "endgame", "momentum"
    asset: str                  # "BTC", "ETH", "XRP", "SOL"
    timeframe: str              # "5m", "15m", "1h"
    market_id: str              # Polymarket condition_id or market slug
    direction: str              # "BUY_YES", "BUY_NO", "BUY_ALL", "LONG", "SHORT"
    entry_price: float          # Expected entry price
    target_price: float         # Expected exit / resolution price
    expected_profit_pct: float  # Expected profit after fees %
    expected_profit_usd: float  # Expected profit in USD
    confidence: float           # 0.0 - 1.0
    urgency: str                # "HIGH", "MEDIUM", "LOW"
    token_ids: Dict[str, str] = field(default_factory=dict)  # outcome -> token_id
    metadata: Dict[str, Any] = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)

    @property
    def risk_reward_ratio(self) -> float:
        if self.entry_price == 0:
            return 0
        return abs(self.target_price - self.entry_price) / self.entry_price

    def __repr__(self):
        return (
            f"Signal({self.strategy}|{self.asset}|{self.timeframe}|"
            f"{self.direction}|profit={self.expected_profit_pct:.2f}%|"
            f"conf={self.confidence:.2f})"
        )


@dataclass
class PriceCandle:
    """Price candle for momentum analysis."""
    timestamp: float
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0


class IntraMarketArbitrage:
    """YES + NO arbitrage when combined price < $1.00 after fees."""

    def __init__(self, config: Optional[IntraMarketConfig] = None):
        self.config = config or IntraMarketConfig()

    def analyze(self, market_data: Dict, asset: str, timeframe: str) -> Optional[ArbitrageSignal]:
        """Check for YES + NO arbitrage opportunity."""
        if not self.config.enabled:
            return None

        yes_data = market_data.get("YES", {})
        no_data = market_data.get("NO", {})

        if not yes_data or not no_data:
            return None

        yes_ask = yes_data.get("ask", 0)
        no_ask = no_data.get("ask", 0)

        if yes_ask <= 0 or no_ask <= 0:
            return None

        total_cost = yes_ask + no_ask
        fee = total_cost * (self.config.fee_pct / 100)
        gas = self.config.gas_cost_usd * 2  # 2 transactions
        total_cost_with_fees = total_cost + fee + gas

        if total_cost_with_fees >= 1.0:
            return None  # No arbitrage opportunity

        profit = 1.0 - total_cost_with_fees
        profit_pct = (profit / total_cost_with_fees) * 100

        if profit_pct < self.config.min_spread_pct:
            return None  # Below minimum threshold

        # Calculate position-scaled profit
        max_shares = self.config.max_position_usd / total_cost_with_fees
        total_profit_usd = profit * max_shares

        # Determine confidence based on spread size and liquidity
        confidence = min(0.95, 0.5 + profit_pct * 0.1)

        return ArbitrageSignal(
            strategy="intra_market",
            asset=asset,
            timeframe=timeframe,
            market_id=market_data.get("condition_id", "unknown"),
            direction="BUY_ALL",
            entry_price=total_cost_with_fees,
            target_price=1.0,
            expected_profit_pct=profit_pct,
            expected_profit_usd=total_profit_usd,
            confidence=confidence,
            urgency="HIGH",
            token_ids={
                "YES": yes_data.get("token_id", ""),
                "NO": no_data.get("token_id", ""),
            },
            metadata={
                "yes_ask": yes_ask,
                "no_ask": no_ask,
                "fee": fee,
                "gas": gas,
                "max_shares": max_shares,
            },
        )


class CombinatorialArbitrage:
    """Multi-outcome arbitrage when sum of prices < $1.00."""

    def __init__(self, config: Optional[CombinatorialConfig] = None):
        self.config = config or CombinatorialConfig()

    def analyze(self, outcomes: List[Dict], asset: str, timeframe: str, market_id: str = "") -> Optional[ArbitrageSignal]:
        """Check if sum of outcome prices < $1.00."""
        if not self.config.enabled:
            return None

        if len(outcomes) < self.config.min_outcomes:
            return None

        total_ask = sum(o.get("ask_price", 0) for o in outcomes)
        if total_ask <= 0:
            return None

        fee_pct = 2.0  # Polymarket fee
        fee = total_ask * (fee_pct / 100)
        gas = 0.007 * len(outcomes)  # One tx per outcome
        total_cost = total_ask + fee + gas

        if total_cost >= 1.0:
            return None

        profit = 1.0 - total_cost
        profit_pct = (profit / total_cost) * 100

        if profit_pct < self.config.min_deviation_pct:
            return None

        max_shares = self.config.max_position_usd / total_cost
        total_profit_usd = profit * max_shares

        token_ids = {o["outcome"]: o["token_id"] for o in outcomes}

        return ArbitrageSignal(
            strategy="combinatorial",
            asset=asset,
            timeframe=timeframe,
            market_id=market_id,
            direction="BUY_ALL",
            entry_price=total_cost,
            target_price=1.0,
            expected_profit_pct=profit_pct,
            expected_profit_usd=total_profit_usd,
            confidence=min(0.90, 0.4 + profit_pct * 0.1),
            urgency="HIGH",
            token_ids=token_ids,
            metadata={
                "num_outcomes": len(outcomes),
                "total_ask": total_ask,
                "outcome_prices": {o["outcome"]: o["ask_price"] for o in outcomes},
            },
        )


class CrossPlatformArbitrage:
    """Price discrepancies between Polymarket and spot exchanges."""

    def __init__(self, config: Optional[CrossPlatformConfig] = None):
        self.config = config or CrossPlatformConfig()
        self._price_history: Dict[str, List[Tuple[float, float]]] = {}  # asset -> [(ts, price)]

    def analyze(self, polymarket_price: float, spot_price: float, strike_price: float,
                market_id: str, asset: str, timeframe: str, is_above_market: bool = True,
                time_to_expiry_hours: float = 24.0, yes_token_id: str = "", no_token_id: str = "") -> Optional[ArbitrageSignal]:
        """Detect cross-platform mispricing."""
        if not self.config.enabled:
            return None

        # Calculate distance from strike
        distance_pct = ((spot_price - strike_price) / strike_price) * 100

        # Estimate "fair" probability based on distance + momentum
        momentum = self._calculate_momentum(asset, spot_price)
        fair_prob = self._estimate_fair_probability(
            distance_pct, momentum, time_to_expiry_hours, is_above_market
        )

        # Calculate mispricing
        if is_above_market:
            # YES = probability of being above strike
            mispricing = fair_prob - polymarket_price
        else:
            # YES = probability of being below strike
            mispricing = fair_prob - polymarket_price

        mispricing_pct = abs(mispricing) * 100

        if mispricing_pct < self.config.min_price_diff_pct:
            return None

        # Determine direction
        if mispricing > 0:
            direction = "BUY_YES"
            entry = polymarket_price
            target = fair_prob
            token_id_used = yes_token_id
        else:
            direction = "BUY_NO"
            entry = 1.0 - polymarket_price
            target = 1.0 - fair_prob
            token_id_used = no_token_id

        fee = entry * 0.02
        profit_pct = ((target - entry - fee) / (entry + fee)) * 100

        if profit_pct <= 0:
            return None

        position_usd = min(self.config.max_position_usd, 500)
        shares = position_usd / (entry + fee)
        profit_usd = (target - entry - fee) * shares

        # Higher confidence when spot is already past strike with momentum
        confidence = min(0.85, 0.3 + mispricing_pct * 0.05 + abs(momentum) * 0.1)

        return ArbitrageSignal(
            strategy="cross_platform",
            asset=asset,
            timeframe=timeframe,
            market_id=market_id,
            direction=direction,
            entry_price=entry,
            target_price=target,
            expected_profit_pct=profit_pct,
            expected_profit_usd=profit_usd,
            confidence=confidence,
            urgency="HIGH" if mispricing_pct > 10 else "MEDIUM",
            token_ids={"YES": yes_token_id, "NO": no_token_id},
            metadata={
                "spot_price": spot_price,
                "strike_price": strike_price,
                "fair_probability": fair_prob,
                "polymarket_price": polymarket_price,
                "mispricing_pct": mispricing_pct,
                "momentum": momentum,
                "distance_pct": distance_pct,
            },
        )

    def update_price_history(self, asset: str, price: float):
        """Update price history for momentum."""
        if asset not in self._price_history:
            self._price_history[asset] = []
        self._price_history[asset].append((time.time(), price))
        # Keep last 1000 data points
        if len(self._price_history[asset]) > 1000:
            self._price_history[asset] = self._price_history[asset][-500:]

    def _calculate_momentum(self, asset: str, current_price: float) -> float:
        """Calculate price momentum."""
        history = self._price_history.get(asset, [])
        if len(history) < 5:
            return 0.0

        # 5-minute momentum
        five_min_ago = time.time() - 300
        old_prices = [p for t, p in history if t < five_min_ago]
        if old_prices:
            old_price = old_prices[-1]
            return ((current_price - old_price) / old_price) * 100
        return 0.0

    def _estimate_fair_probability(self, distance_pct: float, momentum: float,
                                    time_to_expiry_hours: float, is_above: bool) -> float:
        """Estimate fair probability from distance, momentum, and time."""
        if is_above:
            base_prob = 0.5 + (distance_pct * 0.05)  # 1% distance ≈ 5% prob shift
        else:
            base_prob = 0.5 - (distance_pct * 0.05)

        momentum_adj = momentum * 0.03 if is_above else -momentum * 0.03

        # Time decay multiplier
        if time_to_expiry_hours < 1:
            time_multiplier = 2.0  # Very near expiry, probabilities more extreme
        elif time_to_expiry_hours < 6:
            time_multiplier = 1.5
        elif time_to_expiry_hours < 24:
            time_multiplier = 1.2
        else:
            time_multiplier = 1.0

        raw_prob = (base_prob + momentum_adj) * time_multiplier
        return max(0.02, min(0.98, raw_prob))


class EndgameArbitrage:
    """High-probability trades near market resolution."""

    def __init__(self, config: Optional[EndgameConfig] = None):
        self.config = config or EndgameConfig()

    def analyze(self, market_data: Dict, asset: str, spot_price: Optional[float] = None) -> Optional[ArbitrageSignal]:
        """Detect endgame opportunities."""
        if not self.config.enabled:
            return None

        # Parse resolution time
        end_time_str = market_data.get("end_date_iso", "")
        if not end_time_str:
            return None

        try:
            end_time = datetime.fromisoformat(end_time_str.replace("Z", "+00:00"))
        except (ValueError, TypeError):
            return None

        now = datetime.now(timezone.utc)
        hours_to_resolution = (end_time - now).total_seconds() / 3600

        if hours_to_resolution <= 0 or hours_to_resolution > self.config.max_time_to_resolution_hrs:
            return None

        # Check outcome probabilities
        tokens = market_data.get("tokens", [])
        best_outcome = None
        best_probability = 0

        for token in tokens:
            price = float(token.get("price", 0))
            if price > best_probability:
                best_probability = price
                best_outcome = token

        if not best_outcome or best_probability < self.config.min_probability:
            return None

        # Validate with spot price if available
        if spot_price:
            strike = market_data.get("strike_price")
            if strike:
                strike = float(strike)
                is_above = best_outcome.get("outcome", "").upper() == "YES"
                if is_above and spot_price < strike * 0.98:
                    return None  # Spot doesn't support the probability
                elif not is_above and spot_price > strike * 1.02:
                    return None

        # Calculate returns
        entry_price = best_probability
        fee = entry_price * 0.02
        gas = 0.007
        net_cost = entry_price + fee + gas
        profit_per_share = 1.0 - net_cost

        if profit_per_share <= 0:
            return None

        profit_pct = (profit_per_share / net_cost) * 100

        # Annualized return
        if hours_to_resolution > 0:
            annualized = profit_pct * (365 * 24 / hours_to_resolution)
        else:
            annualized = 0

        if annualized < self.config.min_annualized_return_pct:
            return None

        shares = self.config.max_position_usd / net_cost
        profit_usd = profit_per_share * shares

        # Confidence based on probability and time proximity
        confidence = best_probability * 0.9  # Slight discount from raw probability

        return ArbitrageSignal(
            strategy="endgame",
            asset=asset,
            timeframe=f"{hours_to_resolution:.1f}h_to_resolution",
            market_id=market_data.get("condition_id", "unknown"),
            direction=f"BUY_{best_outcome.get('outcome', 'YES').upper()}",
            entry_price=net_cost,
            target_price=1.0,
            expected_profit_pct=profit_pct,
            expected_profit_usd=profit_usd,
            confidence=confidence,
            urgency="HIGH" if hours_to_resolution < 6 else "MEDIUM",
            token_ids={
                best_outcome.get("outcome", "YES"): best_outcome.get("token_id", "")
            },
            metadata={
                "hours_to_resolution": hours_to_resolution,
                "raw_probability": best_probability,
                "annualized_return_pct": annualized,
                "spot_price": spot_price,
            },
        )


class MomentumMeanReversion:
    """Multi-timeframe momentum and mean-reversion strategy."""

    def __init__(self, config: Optional[MomentumConfig] = None):
        self.config = config or MomentumConfig()
        self._candle_buffers: Dict[str, Dict[str, List[PriceCandle]]] = {}
        # {asset: {timeframe: [candles]}}

    def update_candle(self, asset: str, timeframe: str, candle: PriceCandle):
        """Add a new candle to the buffer."""
        if asset not in self._candle_buffers:
            self._candle_buffers[asset] = {}
        if timeframe not in self._candle_buffers[asset]:
            self._candle_buffers[asset][timeframe] = []

        self._candle_buffers[asset][timeframe].append(candle)

        # Trim to max lookback
        max_lookback = max(
            self.config.tf_5m_lookback,
            self.config.tf_15m_lookback,
            self.config.tf_1h_lookback,
        ) + 10
        if len(self._candle_buffers[asset][timeframe]) > max_lookback:
            self._candle_buffers[asset][timeframe] = (
                self._candle_buffers[asset][timeframe][-max_lookback:]
            )

    def analyze(
        self,
        asset: str,
        timeframe: str,
        market_id: str = "",
        yes_token_id: str = "",
        no_token_id: str = "",
        current_yes_price: float = 0.0,
    ) -> Optional[ArbitrageSignal]:
        """Analyze momentum/mean-reversion for a given asset and timeframe."""
        if not self.config.enabled:
            return None

        candles = self._candle_buffers.get(asset, {}).get(timeframe, [])

        # Get timeframe-specific parameters
        params = self._get_tf_params(timeframe)
        if not params:
            return None

        lookback = params["lookback"]
        entry_zscore = params["entry_zscore"]
        take_profit_pct = params["take_profit"]
        stop_loss_pct = params["stop_loss"]

        if len(candles) < lookback:
            return None

        recent = candles[-lookback:]
        closes = np.array([c.close for c in recent])
        volumes = np.array([c.volume for c in recent])

        # Calculate indicators
        zscore = self._zscore(closes)
        rsi = self._rsi(closes)
        roc = self._rate_of_change(closes, period=5)
        vwap_signal = self._vwap_divergence(closes, volumes)

        # Mean reversion buy
        if zscore < -entry_zscore and rsi < 35 and roc > -0.5:
            direction = "BUY_YES"
            entry = current_yes_price if current_yes_price > 0 else closes[-1]
            target = entry * (1 + take_profit_pct / 100)
            profit_pct = take_profit_pct - 2.0  # Minus fees

            if profit_pct <= 0:
                return None

            confidence = min(0.80, 0.3 + abs(zscore) * 0.1 + (35 - rsi) * 0.005)

            return ArbitrageSignal(
                strategy="momentum_mean_reversion",
                asset=asset,
                timeframe=timeframe,
                market_id=market_id,
                direction=direction,
                entry_price=entry,
                target_price=min(target, 0.98),
                expected_profit_pct=profit_pct,
                expected_profit_usd=profit_pct * 5,  # Rough estimate on $500 position
                confidence=confidence,
                urgency="MEDIUM",
                token_ids={"YES": yes_token_id, "NO": no_token_id},
                metadata={
                    "zscore": float(zscore),
                    "rsi": float(rsi),
                    "roc": float(roc),
                    "vwap_signal": vwap_signal,
                    "take_profit_pct": take_profit_pct,
                    "stop_loss_pct": stop_loss_pct,
                    "candles_used": len(recent),
                },
            )

        # Mean reversion sell
        elif zscore > entry_zscore and rsi > 65 and roc < 0.5:
            direction = "BUY_NO"
            entry = 1.0 - (current_yes_price if current_yes_price > 0 else closes[-1])
            target = entry * (1 + take_profit_pct / 100)
            profit_pct = take_profit_pct - 2.0

            if profit_pct <= 0:
                return None

            confidence = min(0.80, 0.3 + abs(zscore) * 0.1 + (rsi - 65) * 0.005)

            return ArbitrageSignal(
                strategy="momentum_mean_reversion",
                asset=asset,
                timeframe=timeframe,
                market_id=market_id,
                direction=direction,
                entry_price=entry,
                target_price=min(target, 0.98),
                expected_profit_pct=profit_pct,
                expected_profit_usd=profit_pct * 5,
                confidence=confidence,
                urgency="MEDIUM",
                token_ids={"YES": yes_token_id, "NO": no_token_id},
                metadata={
                    "zscore": float(zscore),
                    "rsi": float(rsi),
                    "roc": float(roc),
                    "vwap_signal": vwap_signal,
                    "take_profit_pct": take_profit_pct,
                    "stop_loss_pct": stop_loss_pct,
                },
            )

        return None

    def _get_tf_params(self, timeframe: str) -> Optional[Dict]:
        """Get strategy parameters for a specific timeframe."""
        if timeframe == "5m":
            return {
                "lookback": self.config.tf_5m_lookback,
                "entry_zscore": self.config.tf_5m_entry_zscore,
                "take_profit": self.config.tf_5m_take_profit_pct,
                "stop_loss": self.config.tf_5m_stop_loss_pct,
            }
        elif timeframe == "15m":
            return {
                "lookback": self.config.tf_15m_lookback,
                "entry_zscore": self.config.tf_15m_entry_zscore,
                "take_profit": self.config.tf_15m_take_profit_pct,
                "stop_loss": self.config.tf_15m_stop_loss_pct,
            }
        elif timeframe == "1h":
            return {
                "lookback": self.config.tf_1h_lookback,
                "entry_zscore": self.config.tf_1h_entry_zscore,
                "take_profit": self.config.tf_1h_take_profit_pct,
                "stop_loss": self.config.tf_1h_stop_loss_pct,
            }
        return None

    @staticmethod
    def _zscore(data: np.ndarray) -> float:
        """Calculate z-score of the latest value relative to the series."""
        if len(data) < 2:
            return 0.0
        mean = np.mean(data)
        std = np.std(data)
        if std == 0:
            return 0.0
        return float((data[-1] - mean) / std)

    @staticmethod
    def _rsi(data: np.ndarray, period: int = 14) -> float:
        """Calculate RSI (Relative Strength Index)."""
        if len(data) < period + 1:
            period = len(data) - 1
        if period < 2:
            return 50.0

        deltas = np.diff(data[-period - 1:])
        gains = np.where(deltas > 0, deltas, 0)
        losses = np.where(deltas < 0, -deltas, 0)

        avg_gain = np.mean(gains)
        avg_loss = np.mean(losses)

        if avg_loss == 0:
            return 100.0
        rs = avg_gain / avg_loss
        return float(100 - (100 / (1 + rs)))

    @staticmethod
    def _rate_of_change(data: np.ndarray, period: int = 5) -> float:
        """Calculate Rate of Change (ROC) %."""
        if len(data) < period + 1:
            return 0.0
        old = data[-period - 1]
        if old == 0:
            return 0.0
        return float(((data[-1] - old) / old) * 100)

    @staticmethod
    def _vwap_divergence(closes: np.ndarray, volumes: np.ndarray) -> str:
        """Detect price-VWAP divergence."""
        if len(closes) < 3 or np.sum(volumes) == 0:
            return "NEUTRAL"
        vwap = np.sum(closes * volumes) / np.sum(volumes)
        if closes[-1] > vwap * 1.01:
            return "ABOVE_VWAP"
        elif closes[-1] < vwap * 0.99:
            return "BELOW_VWAP"
        return "NEUTRAL"


class StrategyAggregator:
    """Aggregates and ranks strategy signals."""

    URGENCY_WEIGHTS = {"HIGH": 3.0, "MEDIUM": 2.0, "LOW": 1.0}
    STRATEGY_PRIORITY = {
        "intra_market": 1.0,
        "combinatorial": 0.95,
        "endgame": 0.90,
        "cross_platform": 0.80,
        "momentum_mean_reversion": 0.70,
    }

    def __init__(self):
        self.intra = IntraMarketArbitrage()
        self.combinatorial = CombinatorialArbitrage()
        self.cross_platform = CrossPlatformArbitrage()
        self.endgame = EndgameArbitrage()
        self.momentum = MomentumMeanReversion()

    def rank_signals(self, signals: List[ArbitrageSignal]) -> List[ArbitrageSignal]:
        """Rank signals by composite score."""
        scored = []
        for sig in signals:
            score = self._composite_score(sig)
            scored.append((score, sig))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [sig for _, sig in scored]

    def _composite_score(self, signal: ArbitrageSignal) -> float:
        """Calculate composite score."""
        profit_score = min(signal.expected_profit_pct / 10, 1.0)
        confidence_score = signal.confidence
        urgency_score = self.URGENCY_WEIGHTS.get(signal.urgency, 1.0) / 3.0
        strategy_score = self.STRATEGY_PRIORITY.get(signal.strategy, 0.5)
        rr_score = min(signal.risk_reward_ratio * 5, 1.0)

        composite = (
            profit_score * 0.30 +
            confidence_score * 0.25 +
            urgency_score * 0.15 +
            strategy_score * 0.20 +
            rr_score * 0.10
        )
        return composite

    def filter_signals(self, signals: List[ArbitrageSignal], min_confidence: float = 0.4,
                       min_profit_pct: float = 0.5) -> List[ArbitrageSignal]:
        """Filter low-quality signals."""
        return [
            s for s in signals
            if s.confidence >= min_confidence
            and s.expected_profit_pct >= min_profit_pct
        ]
