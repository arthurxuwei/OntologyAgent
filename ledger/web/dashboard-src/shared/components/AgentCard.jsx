// Balance + status card. Number tick animates on balance change.

function useTween(target, duration = 400) {
  const [value, setValue] = React.useState(target);
  const fromRef = React.useRef(target);
  const startRef = React.useRef(performance.now());

  React.useEffect(() => {
    if (value === target) return;
    fromRef.current = value;
    startRef.current = performance.now();
    let raf;
    const tick = () => {
      const t = Math.min(1, (performance.now() - startRef.current) / duration);
      const eased = 1 - Math.pow(1 - t, 3); // ease-out cubic
      setValue(fromRef.current + (target - fromRef.current) * eased);
      if (t < 1) raf = requestAnimationFrame(tick);
      else setValue(target);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [target, duration]);

  return value;
}

// Use the shared formatter so sub-cent (nanopayment) balances render at
// USDC's native 6-decimal precision instead of being rounded to "0.00".
const fmt = (n) => window.formatAmount(n);

const STATUS_CHIP_META = {
  pending_settle: {
    labelKey: 'mvp.dash.status.pending_settle.label',
    tooltipKey: 'mvp.dash.status.pending_settle.tooltip',
  },
  pending_inbound_chain: {
    labelKey: 'mvp.dash.status.pending_inbound_chain.label',
    tooltipKey: 'mvp.dash.status.pending_inbound_chain.tooltip',
  },
  released: {
    labelKey: 'mvp.dash.status.released.label',
    chipColor: 'var(--status-positive)',
  },
  withdrawn: {
    labelKey: 'mvp.dash.status.withdrawn.label',
    chipColor: 'var(--ink-secondary)',
  },
  credited: {
    labelKey: 'mvp.dash.status.credited.label',
    chipColor: 'var(--ink-secondary)',
  },
};

function chipColorFor(meta) {
  return (meta && meta.chipColor) || 'var(--accent-amber)';
}

function ExplanationPopover({ text, anchorRef, onClose, align = 'left' }) {
  React.useEffect(() => {
    const handleMouseDown = (event) => {
      if (anchorRef.current && !anchorRef.current.contains(event.target)) onClose();
    };
    const handleKey = (event) => {
      if (event.key === 'Escape') onClose();
    };
    document.addEventListener('mousedown', handleMouseDown);
    document.addEventListener('keydown', handleKey);
    return () => {
      document.removeEventListener('mousedown', handleMouseDown);
      document.removeEventListener('keydown', handleKey);
    };
  }, [anchorRef, onClose]);

  return (
    <div
      role="tooltip"
      onClick={(event) => event.stopPropagation()}
      style={{
        position: 'absolute',
        top: 'calc(100% + 8px)',
        [align]: 0,
        zIndex: 100,
        width: '320px',
        maxWidth: 'calc(100vw - 32px)',
        padding: '12px 14px',
        background: 'var(--surface-card)',
        border: '1px solid var(--stroke-rule)',
        boxShadow: '0 8px 24px rgba(0,0,0,0.12)',
        fontFamily: 'Geist, -apple-system, sans-serif',
        fontSize: '12px',
        lineHeight: 1.5,
        color: 'var(--ink-secondary)',
        textAlign: 'left',
        whiteSpace: 'normal',
        textTransform: 'none',
        letterSpacing: 0,
        cursor: 'default',
      }}
    >
      {text}
    </div>
  );
}

function InfoTrigger({ tooltipKey, size = 'sm', align = 'left' }) {
  const t = window.useT();
  const [open, setOpen] = React.useState(false);
  const ref = React.useRef(null);
  const iconSize = size === 'lg' ? '13px' : '12px';
  return (
    <span ref={ref} style={{ position: 'relative', display: 'inline-flex', alignItems: 'center' }}>
      <span
        role="button"
        tabIndex={0}
        aria-label={t('mvp.dash.status.info_aria')}
        aria-expanded={open}
        onClick={(event) => {
          event.stopPropagation();
          setOpen((value) => !value);
        }}
        onKeyDown={(event) => {
          if (event.key === 'Enter' || event.key === ' ') {
            event.preventDefault();
            event.stopPropagation();
            setOpen((value) => !value);
          }
        }}
        style={{
          display: 'inline-flex',
          alignItems: 'center',
          cursor: 'pointer',
          color: 'var(--accent-amber)',
          fontSize: iconSize,
          lineHeight: 1,
          opacity: open ? 1 : 0.7,
          userSelect: 'none',
        }}
      >
        ⓘ
      </span>
      {open && (
        <ExplanationPopover
          text={t(tooltipKey)}
          anchorRef={ref}
          onClose={() => setOpen(false)}
          align={align}
        />
      )}
    </span>
  );
}

function PendingBalanceLine({ amount = 0, status = 'pending_settle', size = 'lg' }) {
  if (!amount || amount <= 0) return null;
  const t = window.useT();
  const meta = STATUS_CHIP_META[status];
  const isLg = size === 'lg';
  if (!meta) return null;
  return (
    <div
      style={{
        display: 'inline-flex',
        alignItems: 'center',
        gap: '6px',
        borderLeft: '2px solid var(--accent-amber)',
        paddingLeft: '10px',
        marginTop: isLg ? '16px' : '10px',
      }}
    >
      <span
        className="font-mono"
        style={{
          fontSize: isLg ? '13px' : '11px',
          color: 'var(--accent-amber)',
          letterSpacing: '0.02em',
          whiteSpace: 'nowrap',
        }}
      >
        {t(meta.labelKey)} {window.formatAmount(amount)}
      </span>
      <InfoTrigger tooltipKey={meta.tooltipKey} size={size} />
    </div>
  );
}

window.PendingBalanceLine = PendingBalanceLine;
window.InfoTrigger = InfoTrigger;
window.ExplanationPopover = ExplanationPopover;
window.STATUS_CHIP_META = STATUS_CHIP_META;
window.statusChipColorFor = chipColorFor;

function AgentCard({
  agentName,
  agentRole,
  balance = 0,
  pendingSettlement = 0,
  status = 'unclaimed',
  walletAddress,
  ownerEmail,
  size = 'full',
}) {
  const tweened = useTween(balance);
  // If the target balance has no sub-cent component, snap mid-tween values
  // to 2-decimal precision so the formatter doesn't flicker between
  // "87.234500" (mid-animation) and "87.20" (final) — that toggle would
  // look broken. When the target itself has sub-cent (nanopayment-bearing
  // balance), let the tween show full 6-decimal precision throughout.
  const targetHasSubCent =
    Math.abs(balance - Math.round(balance * 100) / 100) > 1e-9;
  const displayBalance = targetHasSubCent
    ? tweened
    : Math.round(tweened * 100) / 100;
  const [flash, setFlash] = React.useState(false);
  const lastBalRef = React.useRef(balance);
  React.useEffect(() => {
    if (lastBalRef.current !== balance) {
      setFlash(true);
      const t = setTimeout(() => setFlash(false), 600);
      lastBalRef.current = balance;
      return () => clearTimeout(t);
    }
  }, [balance]);

  const statusLabel = {
    unclaimed: 'UNCLAIMED',
    claimed: ownerEmail ? `CLAIMED · ${ownerEmail}` : 'CLAIMED',
    processing: 'PROCESSING',
  }[status];

  const statusColor = status === 'unclaimed'
    ? 'var(--accent-amber)'
    : status === 'processing'
      ? 'var(--accent-amber)'
      : 'var(--ink-primary)';

  const isMini = size === 'mini';

  return (
    <div
      style={{
        background: 'var(--surface-card)',
        border: '1px solid var(--stroke-hairline)',
        padding: isMini ? '20px 24px' : '32px',
        borderRadius: '2px',
      }}
    >
      <div className="flex items-start justify-between">
        <div>
          <div className="font-display" style={{ fontSize: isMini ? '20px' : '28px', fontWeight: 400, letterSpacing: '-0.01em' }}>
            {agentName}
          </div>
          {agentRole && !isMini && (
            <div className="font-body text-sm mt-1" style={{ color: 'var(--ink-secondary)' }}>
              {agentRole}
            </div>
          )}
        </div>
        <span className="pill" style={{ color: statusColor }}>
          {statusLabel}
        </span>
      </div>

      <div
        className={flash ? 'amber-flash' : ''}
        style={{
          marginTop: isMini ? '16px' : '32px',
          padding: '4px 0',
          display: 'flex',
          alignItems: 'baseline',
          gap: '8px',
        }}
      >
        <span className="font-mono" style={{ fontSize: isMini ? '36px' : '56px', letterSpacing: '-0.01em', lineHeight: 1, color: 'var(--ink-primary)' }}>
          {fmt(displayBalance)}
        </span>
        <span className="font-mono text-sm" style={{ color: 'var(--ink-secondary)' }}>USDC</span>
      </div>
      <window.PendingBalanceLine
        amount={pendingSettlement}
        size={isMini ? 'sm' : 'lg'}
      />

      <div className="font-mono text-[11px] mt-6 flex items-center gap-2" style={{ color: 'var(--ink-tertiary)' }}>
        <span>{walletAddress}</span>
        <span style={{ opacity: 0.6 }}>⎘</span>
      </div>
    </div>
  );
}

window.AgentCard = AgentCard;
