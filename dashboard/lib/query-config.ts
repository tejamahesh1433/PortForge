/**
 * Shared TanStack Query timing defaults. Centralized so every hook
 * applies the same policy rather than each picking its own number.
 *
 * STALE_TIME: how long fetched data is considered fresh before TanStack
 * Query will refetch on next mount/focus. 15s balances "reasonably live
 * operational view" against not hammering Central on every navigation.
 *
 * REFETCH_INTERVAL: background auto-refresh while a query is mounted.
 * Applied only to the small set of "always visible" overview-style
 * queries (health, host lists) -- most pages rely on manual refresh
 * (TopBar's refresh control) plus normal refetch-on-mount/focus instead,
 * so a table a user is actively scrolling/reading doesn't jump under
 * them every few seconds.
 */
export const STALE_TIME_MS = 15_000;
export const REFETCH_INTERVAL_MS = 30_000;
