# Market scanner for arbitrage opportunities
import asyncio
import time
import logging
import re
from typing import Any, Dict, List, Optional, Tuple
from collections import defaultdict

from config import (
    CRYPTO_ASSETS, TIMEFRAMES, SCAN_INTERVAL_SECONDS,
    IntraMarketConfig, CombinatorialConfig, CrossPlatformConfig,
    EndgameConfig, MomentumConfig,
)
from api_client import PolymarketClient, CrossPlatformFeeds
from strategies import (
    ArbitrageSignal, PriceCandle,
    IntraMarketArbitrage, CombinatorialArbitrage,
    CrossPlatformArbitrage, EndgameArbitrage,
    MomentumMeanReversion, StrategyAggregator,
)

logger = logging.getLogger("polymarket_arb")


class MarketScanner:
    """Scans Polymarket markets for arbitrage opportunities."""

    def __init__(self):
        self.poly_client = PolymarketClient()
        self.feeds = CrossPlatformFeeds()
        self.aggregator = StrategyAggregator()

        # Market cache
        self._crypto_markets: Dict[str, List[Dict]] = {}
        self._last_discovery_time: float = 0
        self._discovery_interval: float = 300  # Re-discover every 5 min

        # Price candle buffers for momentum
        self._price_snapshots: Dict[str, Dict[str, List[Tuple[float, float]]]] = (
            defaultdict(lambda: defaultdict(list))
        )
        # {asset: {market_id: [(timestamp, yes_price)]}}

        # Statistics
        self.scan_count = 0
        self.signals_found = 0
        self.last_scan_time: float = 0
        self.last_scan_duration: float = 0

    async def initialize(self):
        """Initialize the scanner."""
        logger.info("Initializing Market Scanner...")
        await self._discover_markets()
        logger.info(f"Scanner ready. Tracking markets for: {', '.join(CRYPTO_ASSETS)}")

    async def _discover_markets(self):
        """Discover/refresh all relevant crypto markets on Polymarket."""
        now = time.time()
        if now - self._last_discovery_time < self._discovery_interval:
            return

        logger.info("Discovering crypto prediction markets...")
        self._crypto_markets = await self.poly_client.discover_crypto_markets()
        self._last_discovery_time = now

        # Log discovered markets
        for asset, markets in self._crypto_markets.items():
            for m in markets[:3]:  # Log first 3 per asset
                question = m.get("question", "N/A")[:60]
                logger.info(f"  [{asset}] {question}")

    async def scan_all_timeframes(self) -> List[ArbitrageSignal]:
        """
        Run a complete scan across all assets and timeframes.
        Returns aggregated and ranked signals.
        """
        start_time = time.time()
        self.scan_count += 1
        all_signals: List[ArbitrageSignal] = []

        # Refresh market discovery if needed
        await self._discover_markets()

        # Fetch cross-platform spot prices
        spot_prices = await self.feeds.get_all_prices()

        # Scan each asset
        scan_tasks = []
        for asset in CRYPTO_ASSETS:
            markets = self._crypto_markets.get(asset, [])
            spot = spot_prices.get(asset, {}).get("price")
            scan_tasks.append(self._scan_asset(asset, markets, spot))

        results = await asyncio.gather(*scan_tasks, return_exceptions=True)

        for result in results:
            if isinstance(result, list):
                all_signals.extend(result)
            elif isinstance(result, Exception):
                logger.error(f"Scan error: {result}")

        # Filter and rank
        filtered = self.aggregator.filter_signals(all_signals, min_confidence=0.35, min_profit_pct=0.5)
        ranked = self.aggregator.rank_signals(filtered)

        self.signals_found += len(ranked)
        self.last_scan_time = time.time()
        self.last_scan_duration = time.time() - start_time

        if ranked:
            logger.info(
                f"Scan #{self.scan_count} complete in {self.last_scan_duration:.2f}s | "
                f"Found {len(ranked)} signals (from {len(all_signals)} raw)"
            )
            for i, sig in enumerate(ranked[:5]):
                logger.info(f"  #{i+1} {sig}")
        else:
            logger.debug(f"Scan #{self.scan_count}: No signals ({self.last_scan_duration:.2f}s)")

        return ranked

    async def _scan_asset(
        self,
        asset: str,
        markets: List[Dict],
        spot_price: Optional[float],
    ) -> List[ArbitrageSignal]:
        """Scan all markets for a single asset across all strategies."""
        signals = []

        for market in markets:
            try:
                market_signals = await self._scan_single_market(
                    asset, market, spot_price
                )
                signals.extend(market_signals)
            except Exception as e:
                logger.debug(f"Error scanning market for {asset}: {e}")
                continue

        return signals

    async def _scan_single_market(
        self,
        asset: str,
        market: Dict,
        spot_price: Optional[float],
    ) -> List[ArbitrageSignal]:
        """Run all applicable strategies on a single market."""
        signals = []
        market_id = market.get("condition_id", market.get("id", ""))
        question = market.get("question", "")
        tokens = market.get("tokens", [])

        if not tokens:
            return signals

        # Get current prices
        prices = await self._get_market_prices(market)
        if not prices:
            return signals

        yes_data = prices.get("YES", {})
        no_data = prices.get("NO", {})

        # Strategy 1: Intra-Market Arbitrage
        if yes_data and no_data:
            market_data = {
                "YES": yes_data,
                "NO": no_data,
                "condition_id": market_id,
            }
            for tf in TIMEFRAMES:
                sig = self.aggregator.intra.analyze(market_data, asset, tf)
                if sig:
                    signals.append(sig)
                    break  # One signal per market for intra-market

        # Strategy 2: Combinatorial
        if len(tokens) >= 3:
            outcomes = []
            for token in tokens:
                token_id = token.get("token_id", "")
                price_data = await self.poly_client.get_price(token_id)
                if price_data:
                    outcomes.append({
                        "outcome": token.get("outcome", ""),
                        "token_id": token_id,
                        "ask_price": float(price_data.get("ask", price_data.get("price", 0))),
                    })
            if outcomes:
                sig = self.aggregator.combinatorial.analyze(
                    outcomes, asset, "1h", market_id
                )
                if sig:
                    signals.append(sig)

        # Strategy 3: Cross-Platform
        if spot_price and yes_data:
            strike_price = self._extract_strike_price(question)
            if strike_price:
                is_above = self._is_above_market(question)
                yes_price = yes_data.get("price", 0)

                # Update cross-platform price history
                self.aggregator.cross_platform.update_price_history(asset, spot_price)

                # Parse time to expiry
                end_date = market.get("end_date_iso", "")
                hours_to_expiry = self._calc_hours_to_expiry(end_date)

                for tf in TIMEFRAMES:
                    sig = self.aggregator.cross_platform.analyze(
                        polymarket_price=yes_price,
                        spot_price=spot_price,
                        strike_price=strike_price,
                        market_id=market_id,
                        asset=asset,
                        timeframe=tf,
                        is_above_market=is_above,
                        time_to_expiry_hours=hours_to_expiry,
                        yes_token_id=yes_data.get("token_id", ""),
                        no_token_id=no_data.get("token_id", "") if no_data else "",
                    )
                    if sig:
                        signals.append(sig)

        # Strategy 4: Endgame
        sig = self.aggregator.endgame.analyze(market, asset, spot_price)
        if sig:
            signals.append(sig)

        # Strategy 5: Momentum/Mean-Reversion
        if yes_data:
            yes_price = yes_data.get("price", 0)
            self._update_candle_buffer(
                asset, market_id, yes_price,
                yes_data.get("token_id", ""),
                no_data.get("token_id", "") if no_data else "",
            )

            for tf in TIMEFRAMES:
                sig = self.aggregator.momentum.analyze(
                    asset=asset,
                    timeframe=tf,
                    market_id=market_id,
                    yes_token_id=yes_data.get("token_id", ""),
                    no_token_id=no_data.get("token_id", "") if no_data else "",
                    current_yes_price=yes_price,
                )
                if sig:
                    signals.append(sig)

        return signals

    async def _get_market_prices(self, market: Dict) -> Dict[str, Dict]:
        """Fetch current YES/NO prices for a market."""
        tokens = market.get("tokens", [])
        result = {}

        for token in tokens:
            outcome = token.get("outcome", "").upper()
            token_id = token.get("token_id", "")
            if not token_id:
                continue

            price_data = await self.poly_client.get_price(token_id)
            if price_data:
                result[outcome] = {
                    "token_id": token_id,
                    "price": float(price_data.get("price", 0)),
                    "bid": float(price_data.get("bid", 0)),
                    "ask": float(price_data.get("ask", 0)),
                }

        return result

    def _update_candle_buffer(
        self,
        asset: str,
        market_id: str,
        yes_price: float,
        yes_token_id: str,
        no_token_id: str,
    ):
        """Build candle data from price snapshots for momentum analysis."""
        now = time.time()
        self._price_snapshots[asset][market_id].append((now, yes_price))

        # Trim old data (keep last 2 hours)
        cutoff = now - 7200
        self._price_snapshots[asset][market_id] = [
            (t, p) for t, p in self._price_snapshots[asset][market_id]
            if t > cutoff
        ]

        # Build candles for each timeframe
        for tf_name, tf_seconds in TIMEFRAMES.items():
            snapshots = self._price_snapshots[asset][market_id]
            if len(snapshots) < 3:
                continue

            # Group snapshots into candle periods
            candle_start = snapshots[0][0]
            candle_prices = []
            candles_built = []

            for ts, price in snapshots:
                if ts - candle_start >= tf_seconds:
                    if candle_prices:
                        candle = PriceCandle(
                            timestamp=candle_start,
                            open=candle_prices[0],
                            high=max(candle_prices),
                            low=min(candle_prices),
                            close=candle_prices[-1],
                            volume=len(candle_prices),
                        )
                        candles_built.append(candle)
                    candle_start = ts
                    candle_prices = [price]
                else:
                    candle_prices.append(price)

            # Feed candles to momentum strategy
            for candle in candles_built:
                self.aggregator.momentum.update_candle(asset, tf_name, candle)

    # Helper methods
    @staticmethod
    def _extract_strike_price(question: str) -> Optional[float]:
        """Extract strike price from market question."""
        # Match dollar amounts with optional commas and K/k suffix
        patterns = [
            r'\$([0-9,]+(?:\.[0-9]+)?)\s*[kK]',       # $95k
            r'\$([0-9,]+(?:\.[0-9]+)?)',                 # $100,000
            r'([0-9,]+(?:\.[0-9]+)?)\s*(?:USD|dollars)', # 100000 USD
        ]
        for pattern in patterns:
            match = re.search(pattern, question)
            if match:
                value_str = match.group(1).replace(",", "")
                value = float(value_str)
                if question[match.start():match.end()].lower().endswith('k'):
                    value *= 1000
                return value
        return None

    @staticmethod
    def _is_above_market(question: str) -> bool:
        """Determine if market is about price being ABOVE or BELOW a threshold."""
        q_lower = question.lower()
        above_keywords = ["above", "over", "exceed", "higher than", "reach", ">", "surpass"]
        below_keywords = ["below", "under", "lower than", "drop", "<", "fall"]

        above_score = sum(1 for kw in above_keywords if kw in q_lower)
        below_score = sum(1 for kw in below_keywords if kw in q_lower)

        return above_score >= below_score  # Default to "above"

    @staticmethod
    def _calc_hours_to_expiry(end_date_str: str) -> float:
        """Calculate hours until market resolution."""
        if not end_date_str:
            return 168.0  # Default 1 week
        try:
            from datetime import datetime, timezone
            end = datetime.fromisoformat(end_date_str.replace("Z", "+00:00"))
            now = datetime.now(timezone.utc)
            hours = (end - now).total_seconds() / 3600
            return max(0.1, hours)
        except (ValueError, TypeError):
            return 168.0

    def get_stats(self) -> Dict[str, Any]:
        """Get scanner statistics."""
        return {
            "scan_count": self.scan_count,
            "signals_found": self.signals_found,
            "last_scan_duration_sec": round(self.last_scan_duration, 2),
            "tracked_markets": sum(len(m) for m in self._crypto_markets.values()),
            "markets_per_asset": {
                asset: len(markets) for asset, markets in self._crypto_markets.items()
            },
        }

    async def close(self):
        """Cleanup resources."""
        await self.poly_client.close()
        await self.feeds.close()
