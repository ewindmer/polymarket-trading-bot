
import "dotenv/config";
import { getBitcoinUpDownMarkets, placeOrder, placeSellOrder } from "./bot";
import { BOT_SIDE, BOT_PRICE_CENTS, BOT_CONTRACTS } from "./config";

const DELAY_MS = 5000;

async function main(): Promise<void> {
  console.log("[Test Kalshi] Fetching open Bitcoin 15m up/down markets...");
  const markets = await getBitcoinUpDownMarkets();
  if (markets.length === 0) {
    console.error("[Test Kalshi] No open Bitcoin up/down markets found.");
    process.exit(1);
  }

  const market = markets[0];
  const ticker = market.ticker;
  const side = BOT_SIDE;
  const count = Math.max(1, BOT_CONTRACTS);
  const priceCents = Math.max(1, Math.min(99, BOT_PRICE_CENTS));

  console.log(`[Test Kalshi] Market: ${ticker}`);
  console.log(`[Test Kalshi] Side: ${side} (yes=up, no=down), count=${count}, price=${priceCents}c`);
  console.log("[Test Kalshi] Placing BUY...");

  const buyResult = await placeOrder(ticker, side, count, priceCents, { arbLive: true });

  if ("error" in buyResult) {
    console.error("[Test Kalshi] Buy failed:", buyResult.error);
    process.exit(1);
  }
  console.log("[Test Kalshi] Buy placed:", buyResult.orderId);

  console.log(`[Test Kalshi] Waiting ${DELAY_MS / 1000}s...`);
  await new Promise((r) => setTimeout(r, DELAY_MS));

  console.log("[Test Kalshi] Placing SELL to exit...");
  const sellResult = await placeSellOrder(ticker, side, count, { arbLive: true });

  if ("error" in sellResult) {
    console.error("[Test Kalshi] Sell failed:", sellResult.error);
    process.exit(1);
  }
  console.log("[Test Kalshi] Sell placed:", sellResult.orderId);
  console.log("[Test Kalshi] Done.");
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
