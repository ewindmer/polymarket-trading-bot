import {
  Configuration,
  MarketApi,
  OrdersApi,
  PortfolioApi,
  type Market,
  } from "kalshi-typescript";
  import {
  config,
  BTC_SERIES_TICKER,
  BOT_MAX_MARKETS,
  BOT_SIDE,
  BOT_PRICE_CENTS,
  BOT_CONTRACTS,
  BOT_DRY_RUN,
  } from "./config";

  
  function buildConfiguration(): Configuration {
  return new Configuration({
  apiKey: config.apiKey,
  basePath: config.basePath,
  ...(config.privateKeyPath
  ? { privateKeyPath: config.privateKeyPath }
  : config.privateKeyPem
  ? { privateKeyPem: config.privateKeyPem }
  : {}),
  });
  }



export async function getBitcoinUpDownMarkets(): Promise<Market[]> {
  const msg = " =================== Refreshing markets =================== ";
  console.log(msg);
  appendMonitorLogWithTimestamp(msg);
  const conf = buildConfiguration();
  const marketApi = new MarketApi(conf);
  const all: Market[] = [];
  let cursor: string | undefined;
  let pageSize: number;
  do {
    const res = await marketApi.getMarkets(
      200,
      cursor,
      undefined,
      BTC_SERIES_TICKER,
      undefined,
      undefined,
      undefined,
      undefined,
      undefined,
      undefined,
      undefined,
      "open",
      undefined,
      undefined
    );
    const markets = res.data.markets ?? [];
    pageSize = markets.length;
    all.push(...markets);
    cursor = res.data.cursor ?? undefined;
    if (!cursor || all.length >= BOT_MAX_MARKETS) break;
  } while (pageSize === 200);
  return all.slice(0, BOT_MAX_MARKETS);
}

/** Place a single limit buy order (yes or no) on a market. */
export async function placeOrder(
  ticker: string,
  side: "yes" | "no",
  count: number,
  priceCents: number,
  options?: { arbLive?: boolean }
): Promise<{ orderId: string } | { error: string }> {
  const dryRun = options?.arbLive ? false : BOT_DRY_RUN;
  if (dryRun) {
    const msg = `[DRY RUN] Would place: ticker=${ticker} side=${side} count=${count} yes_price=${priceCents} (no_price for no)`;
    console.log(msg);
    appendMonitorLogWithTimestamp(msg);
    return { orderId: "dry-run" };
  }
  const ordersApi = getOrdersApi();
  const price = Math.max(1, Math.min(99, priceCents));
  try {
    const res = await ordersApi.createOrder({
      ticker,
      side,
      action: "buy",
      count,
      type: "limit",
      time_in_force: "good_till_canceled",
      ...(side === "yes" ? { yes_price: price } : { no_price: price }),
    });
    const order = res.data.order;
    const orderId = order?.order_id ?? "unknown";
    const msg = `Order placed: ${orderId} ticker=${ticker} side=${side} count=${count} price=${price}c`;
    console.log(msg);
    appendMonitorLogWithTimestamp(msg);
    return { orderId };
  } catch (err: unknown) {
    const msg = err instanceof Error ? err.message : String(err);
    console.error(`Order failed for ${ticker}:`, msg);
    appendMonitorLogWithTimestamp(`Order failed for ${ticker}: ${msg}`);
    return { error: msg };
  }
}

/** Place a limit sell order to exit a position (e.g. when arb sum < ARB_SUM_LOW). Uses 1c to take liquidity. */
export async function placeSellOrder(
  ticker: string,
  side: "yes" | "no",
  count: number,
  options?: { arbLive?: boolean }
): Promise<{ orderId: string } | { error: string }> {
  const dryRun = options?.arbLive ? false : BOT_DRY_RUN;
  if (dryRun) {
    const msg = `[DRY RUN] Would sell: ticker=${ticker} side=${side} count=${count}`;
    console.log(msg);
    appendMonitorLogWithTimestamp(msg);
    return { orderId: "dry-run" };
  }
  const ordersApi = getOrdersApi();
  const price = 1; // 1 cent to exit quickly
  try {
    const res = await ordersApi.createOrder({
      ticker,
      side,
      action: "sell",
      count,
      type: "limit",
      time_in_force: "fill_or_kill",
      ...(side === "yes" ? { yes_price: price } : { no_price: price }),
    });
    const order = res.data.order;
    const orderId = order?.order_id ?? "unknown";
    const msg = `Sell order placed: ${orderId} ticker=${ticker} side=${side} count=${count} price=${price}c`;
    console.log(msg);
    appendMonitorLogWithTimestamp(msg);
    return { orderId };
  } catch (err: unknown) {
    const msg = err instanceof Error ? err.message : String(err);
    console.error(`Sell failed for ${ticker}:`, msg);
    appendMonitorLogWithTimestamp(`Sell failed for ${ticker}: ${msg}`);
    return { error: msg };
  }
}