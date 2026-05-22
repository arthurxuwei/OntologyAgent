// Shared amount formatter for USDC values across the demo and dashboard.
//
// Why two decimal regimes:
//   USDC on Base is an ERC-20 token with 6 decimals — the smallest unit is
//   0.000001 USDC ($0.000001 = 1 micro-USDC). Agent-to-agent payments are
//   expected to use the full precision for per-token / per-call billing
//   (e.g. $0.000050 for one model inference), where rounding to two
//   decimals would zero them out.
//
// Display rule (robust to nano-precision):
//   - amount === 0                 → "0.00"
//   - exactly at 2-decimal precision → 2 decimals ("12.50", "1,200.00")
//   - has sub-cent component        → 6 decimals ("0.000050", "2,841.298416")
//
// Why preserve sub-cent in aggregates: the "this system supports
// nanopayments" signal lives precisely in the trailing decimals of
// balances and lifetime totals — rounding them to "$3,500.06" hides the
// fact that the total absorbed micro-billing flows. Users who want
// rounded reporting can always read the first 5 digits and ignore the rest.
//
// Negative sign and thousands separator are handled by toLocaleString.
// Returned string never includes the currency symbol — callers prepend "$".

function formatAmount(n) {
  if (n === 0 || n === undefined || n === null) return '0.00';
  // Use a rounding tolerance so JS float drift (e.g., 0.1 + 0.2) doesn't
  // accidentally trip the "has sub-cent" branch. 1e-9 is well below USDC's
  // 1e-6 native precision, so any real sub-cent value still triggers.
  const rounded = Math.round(n * 100) / 100;
  const hasSubCent = Math.abs(n - rounded) > 1e-9;
  const decimals = hasSubCent ? 6 : 2;
  return n.toLocaleString('en-US', {
    minimumFractionDigits: decimals,
    maximumFractionDigits: decimals,
  });
}

window.formatAmount = formatAmount;
