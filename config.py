# Configuration
import os
from dataclasses import dataclass, field
from typing import Dict, List

# API credentials
POLYMARKET_API_KEY = os.getenv("POLYMARKET_API_KEY", "")
POLYMARKET_SECRET = os.getenv("POLYMARKET_SECRET", "")
POLYMARKET_PASSPHRASE = os.getenv("POLYMARKET_PASSPHRASE", "")
PRIVATE_KEY = os.getenv("POLYGON_PRIVATE_KEY", "")  # Polygon wallet private key

# API endpoints
CLOB_API_URL = "https://clob.polymarket.com"
GAMMA_API_URL = "https://gamma-api.polymarket.com"
WS_URL = "wss://ws-subscriptions-clob.polymarket.com/ws/market"
CHAIN_ID = 137  # Polygon Mainnet

# Cross-platform APIs
BINANCE_API_KEY = os.getenv("BINANCE_API_KEY", "")
BINANCE_SECRET = os.getenv("BINANCE_SECRET", "")
COINGECKO_API_URL = "https://api.coingecko.com/api/v3"

# Crypto targets
CRYPTO_ASSETS = ["BTC", "ETH", "XRP", "SOL"]

# Search keywords to find relevant Polymarket markets
MARKET_SEARCH_KEYWORDS = {
    "BTC": ["Bitcoin", "BTC", "bitcoin price"],
    "ETH": ["Ethereum", "ETH", "ethereum price"],
    "XRP": ["XRP", "Ripple", "xrp price", "XRPL"],
    "SOL": ["Solana", "SOL", "solana price"],
}

# Timeframes
TIMEFRAMES = {
    "5m": 300,       # 5 minutes in seconds
    "15m": 900,      # 15 minutes in seconds
    "1h": 3600,      # 1 hour in seconds
}

# Scan intervals
SCAN_INTERVAL_SECONDS = {
    "5m": 10,    # Scan every 10s for 5m opportunities
    "15m": 30,   # Scan every 30s for 15m opportunities
    "1h": 60,    # Scan every 60s for 1h opportunities
}

# Strategy parameters

@dataclass
class IntraMarketConfig:
    """Intra-market arbitrage config."""
    min_spread_pct: float = 1.5          # Minimum spread % to trigger (after fees)
    max_position_usd: float = 500.0      # Max position per trade
    min_liquidity_usd: float = 100.0     # Min order book liquidity required
    fee_pct: float = 2.0                 # Polymarket fee %
    gas_cost_usd: float = 0.007          # Approx gas per tx on Polygon
    enabled: bool = True

@dataclass
class CombinatorialConfig:
    """Combinatorial arbitrage config."""
    min_deviation_pct: float = 2.0       # Min deviation from $1.00 to trigger
    max_position_usd: float = 300.0
    min_outcomes: int = 3                # Min number of outcomes in market
    enabled: bool = True

@dataclass
class CrossPlatformConfig:
    """Cross-platform arbitrage config."""
    min_price_diff_pct: float = 3.0      # Min price diff % to trigger
    max_position_usd: float = 1000.0
    stale_threshold_sec: int = 30        # Max age of price data
    platforms: List[str] = field(default_factory=lambda: ["binance", "coingecko"])
    enabled: bool = True

@dataclass
class EndgameConfig:
    """Endgame strategy config."""
    min_probability: float = 0.93        # Min YES/NO probability to target
    max_time_to_resolution_hrs: float = 48.0
    min_annualized_return_pct: float = 100.0
    max_position_usd: float = 2000.0
    enabled: bool = True

@dataclass
class MomentumConfig:
    """Momentum/mean-reversion config."""
    # 5-minute scalping
    tf_5m_lookback: int = 12             # 12 candles = 1 hour of 5m data
    tf_5m_entry_zscore: float = 2.0      # Z-score threshold for entry
    tf_5m_take_profit_pct: float = 1.5
    tf_5m_stop_loss_pct: float = 1.0
    # 15-minute swing
    tf_15m_lookback: int = 16            # 16 candles = 4 hours of 15m data
    tf_15m_entry_zscore: float = 1.8
    tf_15m_take_profit_pct: float = 3.0
    tf_15m_stop_loss_pct: float = 2.0
    # 1-hour position
    tf_1h_lookback: int = 24             # 24 candles = 1 day of 1h data
    tf_1h_entry_zscore: float = 1.5
    tf_1h_take_profit_pct: float = 5.0
    tf_1h_stop_loss_pct: float = 3.0
    enabled: bool = True

# Risk management

@dataclass
class RiskConfig:
    max_daily_loss_usd: float = 200.0        # Stop trading if daily loss exceeds this
    max_open_positions: int = 10              # Max simultaneous positions
    max_single_trade_pct: float = 5.0         # Max % of portfolio per trade
    max_portfolio_exposure_pct: float = 60.0  # Max % of portfolio in active trades
    min_portfolio_balance_usd: float = 50.0   # Stop if balance drops below
    cooldown_after_loss_sec: int = 300        # 5 min cooldown after a losing trade
    max_consecutive_losses: int = 5           # Pause after N consecutive losses
    trailing_stop_pct: float = 0.5            # Trailing stop for profitable trades
    initial_capital_usd: float = 1000.0       # Starting capital

# Logging and monitoring
LOG_LEVEL = "INFO"
LOG_FILE = "polymarket_arb_bot.log"
TRADE_LOG_FILE = "trades.json"
PERFORMANCE_LOG_FILE = "performance.csv"
ENABLE_TELEGRAM_ALERTS = False
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

# Execution settings
DRY_RUN = True          # Set to False for live trading (USE WITH CAUTION)
ORDER_TYPE = "FOK"       # Fill-or-Kill for arbitrage precision
SLIPPAGE_TOLERANCE = 0.5  # 0.5% max slippage
