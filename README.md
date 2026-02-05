# Kalshi Trading Bot - Foundation Framework

> **A production-ready foundation for building profitable algorithmic trading bots on Kalshi prediction markets.**
---

## What this Provides

### Core Infrastructure
- **Official Kalshi API Integration** - Uses `kalshi-typescript` SDK
- **Secure Authentication** - API key + RSA private key support
- **Order Management** - Buy/sell execution with limit orders
- **Market Data** - Real-time Bitcoin 15-minute market scanning
- **Risk Controls** - Dry-run mode, configurable position sizing
- **Production Ready** - Error handling, logging, TypeScript

---

## Quick Start

### Prerequisites
- Node.js 18+
- Kalshi account with API access
- Funded trading account
- API key and RSA private key from Kalshi

### Installation

```bash
# Install dependencies
npm install

# Build TypeScript
npm run build
```

### Configuration

Create `.env` file:

```env
# Kalshi API Credentials
KALSHI_API_KEY=your-api-key-id
KALSHI_PRIVATE_KEY_PATH=./private_key.pem

# Trading Configuration
KALSHI_BOT_SIDE=yes              # yes=up, no=down
KALSHI_BOT_PRICE_CENTS=50        # Limit price (1-99 cents)
KALSHI_BOT_CONTRACTS=1           # Position size
KALSHI_BOT_MAX_MARKETS=1         # Markets to monitor
KALSHI_BOT_DRY_RUN=true          # Start in dry-run mode
```

### Test the Bot

```bash
# Run quick trade test (buy then sell after 5s)
npm run bot
```

**Expected Output:**
```
[Test Kalshi] Fetching open Bitcoin 15m up/down markets...
[Test Kalshi] Market: KXBTC15M-24FEB06-T3456
[Test Kalshi] Side: yes (yes=up, no=down), count=1, price=50c
[Test Kalshi] Placing BUY...
[DRY RUN] Would place: ticker=KXBTC15M-24FEB06-T3456 side=yes count=1 yes_price=50
[Test Kalshi] Buy placed: dry-run
[Test Kalshi] Waiting 5s...
[Test Kalshi] Placing SELL to exit...
[DRY RUN] Would sell: ticker=KXBTC15M-24FEB06-T3456 side=yes count=1
[Test Kalshi] Sell placed: dry-run
[Test Kalshi] Done.
```

---

## Project Structure

```
kalshi-trading-bot/
├── bot.ts                  # Core trading functions
├── config.ts               # Configuration & environment
├── quickTrade.ts           # Simple buy/sell test script
├── package.json            # Dependencies
├── tsconfig.json          # TypeScript config
├── .env                    # Your API keys (create this)
└── private_key.pem        # Your RSA key (from Kalshi)
```

---

## Core Functions

### `getBitcoinUpDownMarkets()`
Fetches open Bitcoin 15-minute markets from Kalshi.

```typescript
const markets = await getBitcoinUpDownMarkets();
```

### `placeOrder(ticker, side, count, priceCents)`
Places a limit buy order on a market.

```typescript
const result = await placeOrder(
  "KXBTC15M-24FEB06-T45000",  // Market ticker
  "yes",                       // yes=up, no=down
  10,                          // Number of contracts
  45,                          // Limit price in cents
  { arbLive: true }            // Skip dry-run
);

if ("orderId" in result) {
  console.log("Order placed:", result.orderId);
}
```

### `placeSellOrder(ticker, side, count)`
Places a market sell order to exit position quickly.

```typescript
const result = await placeSellOrder(
  "KXBTC15M-24FEB06-T45000",
  "yes",
  10,
  { arbLive: true }
);
```

---

## Building Your Strategy

This framework handles the "plumbing". Here's how to add profitability:

### 1. Market Analysis
```typescript
function analyzeTrend(market: Market): "bullish" | "bearish" | "neutral" {
  const yesBid = market.yes_bid ?? 0;
  const yesAsk = market.yes_ask ?? 0;
  const spread = yesAsk - yesBid;
  
  if (yesBid > 60 && spread < 5) return "bullish";
  if (yesBid < 40 && spread < 5) return "bearish";
  return "neutral";
}
```

### 2. Signal Generation
```typescript
function getTradeSignal(market: Market): {
  action: "buy" | "sell" | "none",
  side: "yes" | "no",
  confidence: number
} {
  const trend = analyzeTrend(market);
  const volume = market.volume ?? 0;
  
  if (trend === "bullish" && volume > 1000) {
    return { action: "buy", side: "yes", confidence: 0.8 };
  }
  
  return { action: "none", side: "yes", confidence: 0 };
}
```

### 3. Risk Management
```typescript
function calculatePositionSize(
  balance: number,
  confidence: number,
  riskPercent: number = 0.02
): number {
  const maxRisk = balance * riskPercent;
  const adjustedRisk = maxRisk * confidence;
  return Math.floor(adjustedRisk);
}
```

### 4. Execution Loop
```typescript
async function tradingLoop() {
  while (true) {
    const markets = await getBitcoinUpDownMarkets();
    
    for (const market of markets) {
      const signal = getTradeSignal(market);
      
      if (signal.action === "buy") {
        const size = calculatePositionSize(balance, signal.confidence);
        await placeOrder(market.ticker, signal.side, size, 50);
      }
    }
    
    await sleep(30000); // Wait 30s before next check
  }
}
```

---

## Configuration Options

| Variable | Description | Default |
|----------|-------------|---------|
| `KALSHI_API_KEY` | Your API key ID | Required |
| `KALSHI_PRIVATE_KEY_PATH` | Path to RSA private key | `./private_key.pem` |
| `KALSHI_BOT_SIDE` | Trade direction (`yes`/`no`) | `yes` |
| `KALSHI_BOT_PRICE_CENTS` | Limit order price (1-99) | `50` |
| `KALSHI_BOT_CONTRACTS` | Position size per order | `1` |
| `KALSHI_BOT_MAX_MARKETS` | Markets to monitor | `1` |
| `KALSHI_BOT_DRY_RUN` | Test mode (no real orders) | `true` |

---

## Risk Management Features

### Dry-Run Mode
Test your strategy without risking capital:
```env
KALSHI_BOT_DRY_RUN=true
```

### Position Limits
Control maximum exposure:
```env
KALSHI_BOT_CONTRACTS=10          # Max contracts per trade
KALSHI_BOT_MAX_MARKETS=5         # Diversify across markets
```

### Limit Orders
Avoid slippage with limit orders:
```typescript
await placeOrder(ticker, "yes", 10, 45); // Buy at 45¢, not market
```

---

## Trading Strategy Ideas

### Mean Reversion
Buy undervalued outcomes, sell when price rebounds.

### Momentum Trading
Follow strong directional moves in short timeframes.

### Statistical Arbitrage
Exploit price discrepancies between correlated markets.

### Liquidity Provision
Place limit orders and earn the spread.

### News-Based Trading
React to Bitcoin news/events before markets adjust.

---

## Going Live

### Step 1: Backtest
Test your strategy on historical data.

### Step 2: Paper Trade
Run with `DRY_RUN=true` for 1-2 weeks.

### Step 3: Small Capital
Start with $100-500 and 1-2 contracts per trade.

### Step 4: Monitor
Check logs daily, track performance metrics.

### Step 5: Scale Gradually
Increase position size as you prove profitability.

---

## 📈 Performance Tracking

Add logging to track key metrics:

```typescript
interface TradeResult {
  timestamp: Date;
  market: string;
  side: "yes" | "no";
  entryPrice: number;
  exitPrice: number;
  contracts: number;
  pnl: number;
}

const trades: TradeResult[] = [];

trades.push({
  timestamp: new Date(),
  market: ticker,
  side: "yes",
  entryPrice: 45,
  exitPrice: 52,
  contracts: 10,
  pnl: (52 - 45) * 10 * 0.01, // $0.70 profit
});

// Calculate metrics
const totalPnl = trades.reduce((sum, t) => sum + t.pnl, 0);
const winRate = trades.filter(t => t.pnl > 0).length / trades.length;
```

---


## Troubleshooting

### "No open Bitcoin markets found"
→ Markets may not be available 24/7. Check [kalshi.com](https://kalshi.com) for market hours.

### "Authentication failed"
→ Verify your `KALSHI_API_KEY` and `KALSHI_PRIVATE_KEY_PATH` are correct.

### "Order rejected"
→ Check account balance, market status, and price limits (1-99 cents).

### "Dry run mode"
→ Expected! Set `KALSHI_BOT_DRY_RUN=false` only when ready for live trading.

---

## 📚 Resources

- **Kalshi API Docs**: [kalshi.com/docs](https://kalshi.com/docs)
- **Market Data**: Check Kalshi dashboard for active markets
- **TypeScript SDK**: [kalshi-typescript](https://www.npmjs.com/package/kalshi-typescript)

---

## 📝 License

MIT


**Keep your profitable strategies private!** 😉

