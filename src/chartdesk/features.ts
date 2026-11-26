/*
 * Chart Desk: what this build can actually serve.
 *
 * Anything that needs OpenTrader's hosted services, an account, or data our
 * own server does not have yet is hidden, not left on screen dead (owner,
 * 2026-11-26: "don't bloat this"). When a stage adds the data, flip the
 * switch here; every gate reads it from this one file.
 */

/** Symbol search tabs with data behind them (Options: no free source). */
export const SEARCH_TABS_SERVED: ReadonlySet<string> = new Set(["all", "stocks", "funds", "futures", "forex", "crypto", "indices", "bonds", "economy", "local"]);

/** Fundamental studies and the Fundamentals sidebar need the fundamentals endpoint (Stage 5). */
export const FUNDAMENTALS_SERVED = false;

/** Free data has no second or tick bars, ever. */
export const SUB_MINUTE_SERVED = false;

/** Search by ISIN / CUSIP needs a reference database we do not have. */
export const ISIN_SEARCH_SERVED = false;

/** Our data is our own: no "Delayed 15 min" badge, no paid tier, no desktop-app download link. */
export const HOSTED_CHROME = false;
