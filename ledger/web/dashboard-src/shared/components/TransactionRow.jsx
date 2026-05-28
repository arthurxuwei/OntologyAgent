// Compact 2-line transaction row, sized for the narrow KOVALOOP · LEDGER column
// (~340px). Top line: timestamp · role · counterparty · receipt · amount · refund-overlay.
// Bottom line: status pill + optional countdown / verified flag.
// Grid math is intentionally loose so the pill and countdown wrap rather than
// collide with the right-aligned amount.
//
// `role` is optional for backward compat with the demo's stages — when
// passed by the dashboard, a small mono label after the timestamp tells the
// user "what is this agent doing in this trade": AS PAYER (buying), AS PAYEE
// (selling), DEPOSIT (onramp), WITHDRAW (offramp).
//
// Refund visual: instead of striking through the row (which read as "void /
// never happened"), refund rows keep the original signed amount and append
// a tertiary "↩ ±amount · net 0.00" so the round-trip is explicit.
// Line-through is reserved for `expired` — the only status that means the
// trade truly never settled.

function TransactionRow({
  receiptId,
  counterparty,
  amount,
  direction = 'out',
  status = 'released',
  timestamp = 'just now',
  countdown,
  verified,
  role,
  agentTag,
  gasFee,
  gasFeeAtomic,
  netAmount,
  netAmountAtomic,
  txHash,
  network,
}) {
  const t = window.useT();
  const sign = direction === 'out' ? '-' : '+';
  const reverseSign = direction === 'out' ? '+' : '-';
  const arrow = direction === 'out' ? '→' : '←';

  const statusMap = {
    delivery_claimed:  { label: 'DELIVERY CLAIMED',  color: 'var(--accent-amber)',    bg: 'transparent' },
    released:          { label: 'RELEASED',          color: 'var(--status-positive)', bg: 'transparent' },
    refunded:          { label: 'REFUNDED',          color: 'var(--ink-secondary)',   bg: 'transparent' },
    pending_approval:  { label: 'PENDING APPROVAL',  color: 'var(--ink-inverse)',     bg: 'var(--accent-amber)' },
    onramp:            { label: 'ONRAMP',            color: 'var(--ink-secondary)',   bg: 'transparent' },
    pending_settle:       { label: 'SETTLING',         color: 'var(--accent-amber)',    bg: 'transparent' },
    pending_inbound_chain:{ label: 'CREDITING',        color: 'var(--accent-amber)',    bg: 'transparent' },
    withdraw_submitted:   { label: 'Submitted',        color: 'var(--ink-secondary)',   bg: 'transparent' },
    credited:             { label: 'CREDITED',         color: 'var(--status-positive)', bg: 'transparent' },
    withdrawn:            { label: 'WITHDRAWN',        color: 'var(--status-positive)', bg: 'transparent' },
    failed:               { label: 'FAILED',           color: 'var(--status-negative)', bg: 'transparent' },
    // Phase 3 batch 1: added for Stage 9 (m4_n1__internal_expired) — quote
    // pill flips to EXPIRED with neutral ink-tertiary color + line-through.
    expired:           { label: 'EXPIRED',           color: 'var(--ink-tertiary)',    bg: 'transparent' },
  };
  const lifecycleMeta = (window.STATUS_CHIP_META || {})[status];
  const lifecycleLabel = lifecycleMeta ? t(lifecycleMeta.labelKey) : null;
  const s = lifecycleMeta
    ? {
        label: lifecycleLabel,
        color: (window.statusChipColorFor && window.statusChipColorFor(lifecycleMeta)) || 'var(--accent-amber)',
        bg: 'transparent',
      }
    : (statusMap[status] || statusMap.released);
  const isInverse = s.bg !== 'transparent';
  const lineThrough = status === 'expired';
  const isRefunded = status === 'refunded';

  const roleLabel =
    role === 'payer'      ? t('dash.tx.role_payer')
  : role === 'payee'      ? t('dash.tx.role_payee')
  : role === 'deposit'    ? t('dash.tx.role_deposit')
  : role === 'withdrawal' ? t('dash.tx.role_withdrawal')
  : null;

  const atomicToUsdc = (value) => {
    const parsed = Number(value || 0);
    return Number.isFinite(parsed) ? parsed / 1000000 : 0;
  };
  const gasDisplay = gasFee !== undefined
    ? gasFee
    : gasFeeAtomic !== undefined
      ? window.formatAmount(atomicToUsdc(gasFeeAtomic))
      : null;
  const netDisplay = netAmount !== undefined
    ? netAmount
    : netAmountAtomic !== undefined
      ? window.formatAmount(atomicToUsdc(netAmountAtomic))
      : null;
  const withdrawalLike =
    role === 'withdrawal' ||
    status === 'withdraw_submitted' ||
    status === 'withdrawn' ||
    Boolean(gasDisplay || netDisplay);
  const shortTxHash = txHash && txHash.length > 14
    ? `${txHash.slice(0, 6)}…${txHash.slice(-4)}`
    : txHash;

  return (
    <div
      className="hairline-b"
      style={{
        padding: '12px 4px',
        display: 'flex',
        flexDirection: 'column',
        gap: '8px',
      }}
    >
      {/* Top line: timestamp + role + counterparty + receipt id on the left,
          amount + (optional refund overlay) on the right */}
      <div
        className="flex items-baseline"
        style={{ gap: '12px', minWidth: 0, flexWrap: 'wrap' }}
      >
        <div
          className="font-mono text-[11px]"
          style={{ color: 'var(--ink-tertiary)', whiteSpace: 'nowrap' }}
        >
          {timestamp}
        </div>
        {agentTag && (
          <span
            className="font-mono"
            style={{
              fontSize: '10px',
              padding: '2px 6px',
              border: '1px solid var(--stroke-rule)',
              color: 'var(--ink-secondary)',
              letterSpacing: '0.04em',
              whiteSpace: 'nowrap',
            }}
          >
            {agentTag}
          </span>
        )}
        {roleLabel && (
          <span
            className="font-mono"
            style={{
              fontSize: '10px',
              letterSpacing: '0.08em',
              color: 'var(--ink-tertiary)',
              textTransform: 'uppercase',
              whiteSpace: 'nowrap',
            }}
          >
            {roleLabel}
          </span>
        )}
        <div
          className="font-body text-sm flex items-center"
          style={{ color: 'var(--ink-primary)', gap: '6px', minWidth: 0, flex: 1 }}
        >
          <span style={{ color: 'var(--ink-tertiary)' }}>{arrow}</span>
          <span className="truncate">{counterparty}</span>
          <span
            className="font-mono text-[11px]"
            style={{ color: 'var(--ink-tertiary)', whiteSpace: 'nowrap' }}
          >
            {receiptId}
          </span>
        </div>
        <div
          className="font-mono"
          style={{
            fontSize: '15px',
            color: 'var(--ink-primary)',
            whiteSpace: 'nowrap',
            marginLeft: 'auto',
          }}
        >
          {sign}{window.formatAmount(amount)}
        </div>
        {isRefunded && (
          <div
            className="font-mono"
            style={{
              fontSize: '12px',
              color: 'var(--ink-tertiary)',
              whiteSpace: 'nowrap',
              letterSpacing: '0.02em',
            }}
            title={t('dash.tx.refund_tooltip')}
          >
            ↩ {reverseSign}{window.formatAmount(amount)} · {t('dash.tx.refund_net')}
          </div>
        )}
      </div>

      {/* Bottom line: status pill + optional countdown / verified */}
      <div className="flex items-center flex-wrap" style={{ gap: '8px' }}>
        <span
          className="pill"
          style={{
            color: isInverse ? 'var(--ink-inverse)' : s.color,
            background: s.bg,
            borderColor: isInverse ? s.bg : s.color,
            textDecoration: lineThrough ? 'line-through' : 'none',
          }}
        >
          {s.label}
        </span>
        {lifecycleMeta && lifecycleMeta.tooltipKey && window.InfoTrigger && (
          <window.InfoTrigger tooltipKey={lifecycleMeta.tooltipKey} size="sm" />
        )}
        {countdown && (
          <span
            className="font-mono text-[11px]"
            style={{ color: 'var(--accent-amber)', whiteSpace: 'nowrap' }}
          >
            {countdown}
          </span>
        )}
        {verified && (
          <span
            className="font-mono text-[11px]"
            style={{ color: 'var(--status-positive)', whiteSpace: 'nowrap' }}
          >
            ✓ verified
          </span>
        )}
        {withdrawalLike && (gasDisplay || netDisplay) && (
          <span className="font-mono text-[11px]" style={{ color: 'var(--ink-tertiary)' }}>
            {gasDisplay ? `Gas ~${gasDisplay}` : ''}
            {gasDisplay && netDisplay ? ' · ' : ''}
            {netDisplay ? `Net ${netDisplay}` : ''}
            {(gasDisplay || netDisplay) && network ? ` · ${network}` : ''}
          </span>
        )}
        {txHash && (
          <span
            className="font-mono text-[11px]"
            style={{ color: 'var(--ink-tertiary)', whiteSpace: 'nowrap' }}
            title={txHash}
          >
            {shortTxHash}
          </span>
        )}
      </div>
    </div>
  );
}

window.TransactionRow = TransactionRow;
