export const BOT_SIDE = (process.env.KALSHI_BOT_SIDE ?? "yes") as "yes" | "no";

/** Bot: limit price in cents (1–99). Use ask to take liquidity. */
export const BOT_PRICE_CENTS = parseInt(
  process.env.KALSHI_BOT_PRICE_CENTS ?? "50",
  10
);

/** Bot: contracts per order */
export const BOT_CONTRACTS = parseInt(
  process.env.KALSHI_BOT_CONTRACTS ?? "1",
  10
);

export const BTC_SERIES_TICKER = "KXBTC15M";

export const BOT_MAX_MARKETS = parseInt(
    process.env.KALSHI_BOT_MAX_MARKETS ?? "1",
    10
    );
export const BOT_DRY_RUN = process.env.KALSHI_BOT_DRY_RUN === "true";