// MVP AddressPicker — custom dropdown for choosing among saved external
// withdrawal addresses. Mounted on FundingView (Step 2) and later on
// SettingsView (Step 5) for management.
//
// Why custom (not native <select>): we render a star marker for the default
// wallet, two-line items (label + truncated address), and a trailing
// "+ Add new" action — none of which native <select> supports.
//
// Selection model: controlled. Parent owns `selectedId` and reacts to
// `onSelect`. Default-wallet logic is read from useAppState() purely for
// rendering the ★ marker — selection itself is independent so the user can
// override per-transaction without touching the persisted default.

(function () {
  function AddressPicker({
    selectedId,
    onSelect,
    onAddNew,
    disabled = false,
  }) {
    const t = window.useT();
    const { wallets, defaultWalletId } = window.useAppState();
    const [open, setOpen] = React.useState(false);
    const rootRef = React.useRef(null);

    // Close on outside click or Esc.
    React.useEffect(() => {
      if (!open) return;
      const handleClick = (e) => {
        if (rootRef.current && !rootRef.current.contains(e.target)) {
          setOpen(false);
        }
      };
      const handleKey = (e) => {
        if (e.key === 'Escape') setOpen(false);
      };
      document.addEventListener('mousedown', handleClick);
      document.addEventListener('keydown', handleKey);
      return () => {
        document.removeEventListener('mousedown', handleClick);
        document.removeEventListener('keydown', handleKey);
      };
    }, [open]);

    const selected = wallets.find((w) => w.id === selectedId);
    const isEmpty = wallets.length === 0;

    const handleToggle = () => {
      if (disabled) return;
      setOpen((o) => !o);
    };

    const handlePick = (id) => {
      if (onSelect) onSelect(id);
      setOpen(false);
    };

    const handleAddNew = () => {
      setOpen(false);
      if (onAddNew) onAddNew();
    };

    return (
      <div ref={rootRef} style={{ position: 'relative', width: '100%' }}>
        {/* Closed state — looks like an input but is a button. */}
        <button
          type="button"
          onClick={handleToggle}
          disabled={disabled}
          className="font-body"
          style={{
            width: '100%',
            padding: '10px 14px',
            border: '1px solid var(--stroke-rule)',
            background: disabled ? 'var(--stroke-hairline)' : 'var(--surface-paper)',
            cursor: disabled ? 'not-allowed' : 'pointer',
            fontSize: '14px',
            color: 'var(--ink-primary)',
            outline: 'none',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            textAlign: 'left',
            gap: '12px',
            minHeight: '42px',
          }}
        >
          {isEmpty ? (
            <span style={{ color: 'var(--ink-tertiary)', fontSize: '13px' }}>
              {t('mvp.dash.addr_book.empty_inline')}
            </span>
          ) : selected ? (
            <span style={{ display: 'flex', alignItems: 'center', gap: '10px', minWidth: 0, flex: 1 }}>
              {selected.id === defaultWalletId && (
                <span style={{ color: 'var(--accent-amber)', fontSize: '13px', flexShrink: 0 }}>★</span>
              )}
              <span style={{ fontWeight: 500, flexShrink: 0 }}>{selected.label}</span>
              <ChainChip chain={selected.chain} />
              <span
                className="font-mono"
                style={{ color: 'var(--ink-tertiary)', fontSize: '12px', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}
              >
                {truncateAddress(selected.address)}
              </span>
            </span>
          ) : (
            <span style={{ color: 'var(--ink-tertiary)', fontSize: '13px' }}>
              {t('mvp.dash.addr_book.placeholder')}
            </span>
          )}
          <span style={{ color: 'var(--ink-tertiary)', fontSize: '10px', userSelect: 'none', flexShrink: 0 }}>
            {open ? '▲' : '▼'}
          </span>
        </button>

        {open && (
          <div
            style={{
              position: 'absolute',
              top: 'calc(100% + 4px)',
              left: 0,
              right: 0,
              background: 'var(--surface-paper)',
              border: '1px solid var(--stroke-rule)',
              boxShadow: '0 4px 16px rgba(26, 26, 26, 0.12)',
              zIndex: 10,
              maxHeight: '320px',
              overflowY: 'auto',
            }}
          >
            {isEmpty ? (
              <div style={{ padding: '20px 14px', textAlign: 'center' }}>
                <div
                  className="font-body"
                  style={{
                    fontSize: '13px',
                    color: 'var(--ink-tertiary)',
                    marginBottom: '12px',
                  }}
                >
                  {t('mvp.dash.addr_book.empty_title')}
                </div>
                <button
                  type="button"
                  onClick={handleAddNew}
                  className="font-body"
                  style={{
                    background: 'var(--ink-primary)',
                    border: '1px solid var(--ink-primary)',
                    color: 'var(--ink-inverse)',
                    padding: '8px 16px',
                    fontSize: '13px',
                    cursor: 'pointer',
                    letterSpacing: '0.02em',
                  }}
                >
                  {t('mvp.dash.addr_book.empty_cta')}
                </button>
              </div>
            ) : (
              <>
                {wallets.map((w) => (
                  <button
                    key={w.id}
                    type="button"
                    onClick={() => handlePick(w.id)}
                    className="font-body"
                    style={{
                      display: 'block',
                      width: '100%',
                      padding: '10px 14px',
                      background: w.id === selectedId ? 'var(--accent-amber-soft)' : 'transparent',
                      border: 'none',
                      borderBottom: '1px solid var(--stroke-hairline)',
                      cursor: 'pointer',
                      textAlign: 'left',
                      fontSize: '13px',
                      color: 'var(--ink-primary)',
                    }}
                  >
                    <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                      {w.id === defaultWalletId ? (
                        <span style={{ color: 'var(--accent-amber)', fontSize: '12px', width: '12px', flexShrink: 0 }}>★</span>
                      ) : (
                        <span style={{ width: '12px', flexShrink: 0 }} />
                      )}
                      <span style={{ fontWeight: 500 }}>{w.label}</span>
                      <ChainChip chain={w.chain} />
                    </div>
                    <div
                      className="font-mono"
                      style={{
                        marginTop: '2px',
                        marginLeft: '20px',
                        fontSize: '11px',
                        color: 'var(--ink-tertiary)',
                        overflow: 'hidden',
                        textOverflow: 'ellipsis',
                        whiteSpace: 'nowrap',
                      }}
                    >
                      {truncateAddress(w.address)}
                    </div>
                  </button>
                ))}
                <button
                  type="button"
                  onClick={handleAddNew}
                  className="font-body"
                  style={{
                    display: 'block',
                    width: '100%',
                    padding: '10px 14px',
                    background: 'transparent',
                    border: 'none',
                    cursor: 'pointer',
                    textAlign: 'left',
                    fontSize: '13px',
                    color: 'var(--accent-amber)',
                    letterSpacing: '0.01em',
                  }}
                >
                  + {t('mvp.dash.addr_book.add_new')}
                </button>
              </>
            )}
          </div>
        )}
      </div>
    );
  }

  function truncateAddress(addr) {
    if (!addr) return '';
    if (addr.length <= 13) return addr;
    return `${addr.slice(0, 6)}…${addr.slice(-4)}`;
  }

  // Small lozenge that surfaces the wallet's destination chain. Static for
  // MVP (Base is the only enabled chain) but kept as a component so adding
  // more chains later only touches this one place.
  function ChainChip({ chain }) {
    const label = (chain || 'base').toUpperCase();
    return (
      <span
        className="smallcaps-mono"
        style={{
          display: 'inline-block',
          padding: '1px 6px',
          fontSize: '9px',
          letterSpacing: '0.06em',
          color: 'var(--ink-secondary)',
          border: '1px solid var(--stroke-rule)',
          background: 'var(--surface-paper)',
          lineHeight: 1.5,
          flexShrink: 0,
        }}
      >
        {label}
      </span>
    );
  }

  window.AddressPicker = AddressPicker;
})();
