# Polymarket API client and cross-platform price feeds
import asyncio
import json
import time
import logging
import hmac
import hashlib
import base64
from typing import Any, Dict, List, Optional, Tuple
from datetime import datetime, timezone

import aiohttp
import websockets

from config import (
    CLOB_API_URL, GAMMA_API_URL, WS_URL,
    POLYMARKET_API_KEY, POLYMARKET_SECRET, POLYMARKET_PASSPHRASE,
    BINANCE_API_KEY, BINANCE_SECRET, COINGECKO_API_URL,
    CRYPTO_ASSETS, MARKET_SEARCH_KEYWORDS,
    ORDER_TYPE, SLIPPAGE_TOLERANCE, DRY_RUN, CHAIN_ID,
)

logger = logging.getLogger("polymarket_arb")


class PolymarketClient:
    """Polymarket CLOB API client."""

    def __init__(self):
        self.base_url = CLOB_API_URL
        self.gamma_url = GAMMA_API_URL
        self.api_key = POLYMARKET_API_KEY
        self.secret = POLYMARKET_SECRET
        self.passphrase = POLYMARKET_PASSPHRASE
        self.session: Optional[aiohttp.ClientSession] = None
        self._rate_limit_remaining = 100
        self._last_request_time = 0
        self._min_request_interval = 0.6  # ~100 req/min

    async def _ensure_session(self):
        if self.session is None or self.session.closed:
            self.session = aiohttp.ClientSession(
                headers=self._auth_headers(),
                timeout=aiohttp.ClientTimeout(total=10)
            )

    def _auth_headers(self) -> Dict[str, str]:
        timestamp = str(int(time.time()))
        message = timestamp + "GET" + "/auth"
        if self.secret:
            signature = hmac.new(
                base64.b64decode(self.secret),
                message.encode("utf-8"),
                hashlib.sha256
            ).hexdigest()
        else:
            signature = ""
        return {
            "POLY-API-KEY": self.api_key,
            "POLY-SIGNATURE": signature,
            "POLY-TIMESTAMP": timestamp,
            "POLY-PASSPHRASE": self.passphrase,
            "Content-Type": "application/json",
        }

    async def _rate_limit(self):
        """Enforce rate limiting."""
        now = time.time()
        elapsed = now - self._last_request_time
        if elapsed < self._min_request_interval:
            await asyncio.sleep(self._min_request_interval - elapsed)
        self._last_request_time = time.time()

    async def _get(self, endpoint: str, params: Optional[Dict] = None) -> Any:
        await self._ensure_session()
        await self._rate_limit()
        url = f"{self.base_url}{endpoint}"
        try:
            async with self.session.get(url, params=params) as resp:
                if resp.status == 200:
                    return await resp.json()
                elif resp.status == 429:
                    logger.warning("Rate limited, backing off 60s...")
                    await asyncio.sleep(60)
                    return await self._get(endpoint, params)
                else:
                    text = await resp.text()
                    logger.error(f"API error {resp.status}: {text}")
                    return None
        except Exception as e:
            logger.error(f"Request failed: {e}")
            return None

    async def _post(self, endpoint: str, data: Dict) -> Any:
        await self._ensure_session()
        await self._rate_limit()
        url = f"{self.base_url}{endpoint}"
        try:
            async with self.session.post(url, json=data) as resp:
                if resp.status in (200, 201):
                    return await resp.json()
                else:
                    text = await resp.text()
                    logger.error(f"POST error {resp.status}: {text}")
                    return None
        except Exception as e:
            logger.error(f"POST failed: {e}")
            return None

    # Market discovery
    async def get_markets(self, next_cursor: str = "") -> Dict:
        """Fetch all active markets from the CLOB API."""
        params = {"active": "true"}
        if next_cursor:
            params["next_cursor"] = next_cursor
        return await self._get("/markets", params)

    async def search_gamma_markets(self, query: str) -> List[Dict]:
        """Search for markets via the Gamma API (richer metadata)."""
        await self._ensure_session()
        url = f"{self.gamma_url}/markets"
        params = {"closed": "false", "limit": 50}
        try:
            async with self.session.get(url, params=params) as resp:
                if resp.status == 200:
                    markets = await resp.json()
                    # Filter by search query
                    return [
                        m for m in markets
                        if query.lower() in m.get("question", "").lower()
                        or query.lower() in m.get("description", "").lower()
                        or query.lower() in str(m.get("tags", [])).lower()
                    ]
                return []
        except Exception as e:
            logger.error(f"Gamma search failed: {e}")
            return []

    async def discover_crypto_markets(self) -> Dict[str, List[Dict]]:
        """Discover all active crypto prediction markets for our target assets."""
        crypto_markets = {asset: [] for asset in CRYPTO_ASSETS}
        for asset, keywords in MARKET_SEARCH_KEYWORDS.items():
            for keyword in keywords:
                markets = await self.search_gamma_markets(keyword)
                for m in markets:
                    if m not in crypto_markets[asset]:
                        crypto_markets[asset].append(m)
                await asyncio.sleep(0.5)  # Gentle rate limiting
        total = sum(len(v) for v in crypto_markets.values())
        logger.info(f"Discovered {total} crypto markets: " +
                    ", ".join(f"{k}={len(v)}" for k, v in crypto_markets.items()))
        return crypto_markets

    # Order book
    async def get_order_book(self, token_id: str) -> Optional[Dict]:
        """Get the full order book for a token."""
        return await self._get(f"/book", {"token_id": token_id})

    async def get_midpoint(self, token_id: str) -> Optional[float]:
        """Get the midpoint price for a token."""
        data = await self._get(f"/midpoint", {"token_id": token_id})
        if data and "mid" in data:
            return float(data["mid"])
        return None

    async def get_price(self, token_id: str) -> Optional[Dict]:
        """Get best bid/ask for a token."""
        data = await self._get(f"/price", {"token_id": token_id})
        return data

    async def get_spread(self, token_id: str) -> Optional[Dict]:
        """Get the bid-ask spread for a token."""
        return await self._get(f"/spread", {"token_id": token_id})

    async def get_prices_for_market(self, condition_id: str) -> Dict[str, Dict]:
        """Get YES and NO prices for a binary market."""
        market = await self._get(f"/markets/{condition_id}")
        if not market:
            return {}
        tokens = market.get("tokens", [])
        result = {}
        for token in tokens:
            outcome = token.get("outcome", "").upper()
            token_id = token.get("token_id", "")
            price_data = await self.get_price(token_id)
            if price_data:
                result[outcome] = {
                    "token_id": token_id,
                    "price": float(price_data.get("price", 0)),
                    "bid": float(price_data.get("bid", 0)),
                    "ask": float(price_data.get("ask", 0)),
                }
        return result

    # Trading
    async def place_order(
        self,
        token_id: str,
        side: str,        # "BUY" or "SELL"
        price: float,
        size: float,
        order_type: str = ORDER_TYPE,
    ) -> Optional[Dict]:
        """Place an order on the CLOB."""
        if DRY_RUN:
            logger.info(f"[DRY RUN] Order: {side} {size:.2f} @ ${price:.4f} "
                        f"token={token_id[:16]}... type={order_type}")
            return {
                "id": f"dry_run_{int(time.time()*1000)}",
                "status": "SIMULATED",
                "side": side,
                "price": price,
                "size": size,
                "token_id": token_id,
            }

        order_data = {
            "tokenID": token_id,
            "price": price,
            "size": size,
            "side": side,
            "type": order_type,
            "feeRateBps": 0,
        }
        return await self._post("/order", order_data)

    async def place_market_order(
        self,
        token_id: str,
        side: str,
        amount_usd: float,
    ) -> Optional[Dict]:
        """Place a market order (FOK at best available price)."""
        book = await self.get_order_book(token_id)
        if not book:
            return None

        if side == "BUY":
            asks = book.get("asks", [])
            if not asks:
                return None
            best_ask = float(asks[0]["price"])
            price = best_ask * (1 + SLIPPAGE_TOLERANCE / 100)
            size = amount_usd / best_ask
        else:
            bids = book.get("bids", [])
            if not bids:
                return None
            best_bid = float(bids[0]["price"])
            price = best_bid * (1 - SLIPPAGE_TOLERANCE / 100)
            size = amount_usd / best_bid

        return await self.place_order(token_id, side, price, size, "FOK")

    async def cancel_order(self, order_id: str) -> Optional[Dict]:
        """Cancel an active order."""
        if DRY_RUN:
            logger.info(f"[DRY RUN] Cancel order: {order_id}")
            return {"status": "CANCELLED"}
        return await self._post(f"/cancel", {"orderID": order_id})

    async def get_open_orders(self) -> List[Dict]:
        """Get all open orders."""
        data = await self._get("/orders/active")
        return data if data else []

    async def get_balance(self) -> float:
        """Get USDC balance (simplified)."""
        # In production, query the Polygon USDC contract directly
        data = await self._get("/balances")
        if data and "balance" in data:
            return float(data["balance"])
        return 0.0

    # WebSocket
    async def subscribe_orderbook(
        self,
        token_ids: List[str],
        callback,
    ):
        """Subscribe to real-time order book updates via WebSocket."""
        while True:
            try:
                async with websockets.connect(WS_URL) as ws:
                    subscribe_msg = {
                        "type": "subscribe",
                        "channel": "book",
                        "assets": token_ids,
                    }
                    await ws.send(json.dumps(subscribe_msg))
                    logger.info(f"WebSocket subscribed to {len(token_ids)} tokens")

                    async for message in ws:
                        data = json.loads(message)
                        await callback(data)

            except (websockets.exceptions.ConnectionClosed, Exception) as e:
                logger.warning(f"WebSocket disconnected: {e}. Reconnecting in 5s...")
                await asyncio.sleep(5)

    async def close(self):
        if self.session and not self.session.closed:
            await self.session.close()


class CrossPlatformFeeds:
    """Fetch prices from Binance and CoinGecko."""

    COINGECKO_IDS = {
        "BTC": "bitcoin",
        "ETH": "ethereum",
        "XRP": "ripple",
        "SOL": "solana",
    }
    BINANCE_SYMBOLS = {
        "BTC": "BTCUSDT",
        "ETH": "ETHUSDT",
        "XRP": "XRPUSDT",
        "SOL": "SOLUSDT",
    }

    def __init__(self):
        self.session: Optional[aiohttp.ClientSession] = None
        self._price_cache: Dict[str, Dict] = {}  # {asset: {price, timestamp, source}}

    async def _ensure_session(self):
        if self.session is None or self.session.closed:
            self.session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=10)
            )

    async def get_binance_price(self, asset: str) -> Optional[float]:
        """Get current spot price from Binance."""
        symbol = self.BINANCE_SYMBOLS.get(asset)
        if not symbol:
            return None
        await self._ensure_session()
        try:
            url = f"https://api.binance.com/api/v3/ticker/price?symbol={symbol}"
            async with self.session.get(url) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    price = float(data["price"])
                    self._price_cache[asset] = {
                        "price": price,
                        "timestamp": time.time(),
                        "source": "binance",
                    }
                    return price
        except Exception as e:
            logger.error(f"Binance price fetch failed for {asset}: {e}")
        return None

    async def get_coingecko_price(self, asset: str) -> Optional[float]:
        """Get current spot price from CoinGecko."""
        cg_id = self.COINGECKO_IDS.get(asset)
        if not cg_id:
            return None
        await self._ensure_session()
        try:
            url = f"{COINGECKO_API_URL}/simple/price?ids={cg_id}&vs_currencies=usd"
            async with self.session.get(url) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    price = data[cg_id]["usd"]
                    self._price_cache[asset] = {
                        "price": price,
                        "timestamp": time.time(),
                        "source": "coingecko",
                    }
                    return float(price)
        except Exception as e:
            logger.error(f"CoinGecko price fetch failed for {asset}: {e}")
        return None

    async def get_all_prices(self) -> Dict[str, Dict]:
        """Fetch prices for all crypto assets from multiple sources."""
        results = {}
        tasks = []
        for asset in CRYPTO_ASSETS:
            tasks.append(self._fetch_asset_price(asset))
        fetched = await asyncio.gather(*tasks, return_exceptions=True)
        for asset, data in zip(CRYPTO_ASSETS, fetched):
            if isinstance(data, dict):
                results[asset] = data
        return results

    async def _fetch_asset_price(self, asset: str) -> Dict:
        """Fetch price from Binance first, fallback to CoinGecko."""
        price = await self.get_binance_price(asset)
        source = "binance"
        if price is None:
            price = await self.get_coingecko_price(asset)
            source = "coingecko"
        return {
            "asset": asset,
            "price": price,
            "source": source,
            "timestamp": time.time(),
        }

    def get_cached_price(self, asset: str, max_age_sec: int = 30) -> Optional[float]:
        """Get cached price if fresh enough."""
        cached = self._price_cache.get(asset)
        if cached and (time.time() - cached["timestamp"]) < max_age_sec:
            return cached["price"]
        return None

    async def close(self):
        if self.session and not self.session.closed:
            await self.session.close()
