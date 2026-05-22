// MVP · AddAddressModal — adds a wallet to the owner's address book.
//
// State machine (single form with a transforming body):
//   choose  → user picks method (Scan QR / Paste)
//   scan    → QRScanner is mounted; onDecode flips to confirm
//   paste   → 0x… text input + Continue button → confirm on valid input
//   confirm → captured address (readonly) + label + "set as default" + Save
//
// Both methods feed the same final form because the data we collect is the
// same regardless of input source — only the capture step differs. Buttons
// for Scan and Paste stay symmetrical on desktop AND mobile (camera might be
// unavailable, but the picker hides under camera-denied/no-camera overlays,
// and Paste is always there as the deterministic fallback).
//
// On Save: window.useAppState().addWallet(label, address, { setAsDefault })
// is called and the modal closes. The picker upstream re-renders against the
// new wallets[] automatically (context update).

(function () {
  const STRICT_ADDR_RE = /^0x[a-fA-F0-9]{40}$/;

  function AddAddressModal({ open, onClose }) {
    const t = window.useT();
    const { wallets, addWallet } = window.useAppState();

    // mode + per-mode draft state, all reset on open.
    const [mode, setMode]               = React.useState('choose');
    const [address, setAddress]         = React.useState('');
    const [pasteInput, setPasteInput]   = React.useState('');
    const [label, setLabel]             = React.useState('');
    const [setAsDefault, setSetAsDefault] = React.useState(false);

    React.useEffect(() => {
      if (open) {
        setMode('choose');
        setAddress('');
        setPasteInput('');
        setLabel('');
        setSetAsDefault(false);
      }
    }, [open]);

    // Esc closes (any mode).
    React.useEffect(() => {
      if (!open) return;
      const onKey = (e) => { if (e.key === 'Escape') onClose && onClose(); };
      document.addEventListener('keydown', onKey);
      return () => document.removeEventListener('keydown', onKey);
    }, [open, onClose]);

    if (!open) return null;

    // Case-insensitive duplicate check against existing wallets.
    const normalizedAddress = address.toLowerCase();
    const duplicate = !!normalizedAddress && wallets.some(
      (w) => w.address.toLowerCase() === normalizedAddress
    );

    const pasteValid = STRICT_ADDR_RE.test(pasteInput.trim());

    const handleBack = () => {
      // Return to method picker; discard whatever was captured.
      setMode('choose');
      setAddress('');
      setPasteInput('');
    };

    const handleScanDecoded = (addr) => {
      setAddress(addr);
      // Default label suggestion: "Wallet N+1".
      setLabel('');
      setMode('confirm');
    };

    const handlePasteContinue = () => {
      if (!pasteValid) return;
      setAddress(pasteInput.trim());
      setMode('confirm');
    };

    const handleSave = () => {
      if (!address || duplicate) return;
      const finalLabel = label.trim() || `Wallet ${wallets.length + 1}`;
      // MVP locks chain to 'base'. Selector skeleton in Confirm exposes the
      // future multi-chain UI but is non-interactive until phase 2 / CCTP.
      addWallet(finalLabel, address, { chain: 'base', setAsDefault });
      onClose && onClose();
    };

    return (
      <div
        onClick={(e) => { if (e.target === e.currentTarget) onClose && onClose(); }}
        style={{
          position: 'fixed',
          inset: 0,
          background: 'rgba(26, 26, 26, 0.55)',
          zIndex: 100,
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          padding: '32px',
        }}
      >
        <div
          style={{
            width: '480px',
            maxWidth: '100%',
            maxHeight: 'calc(100vh - 64px)',
            overflowY: 'auto',
            background: 'var(--surface-card)',
            border: '1px solid var(--stroke-rule)',
            boxShadow: '0 24px 64px rgba(0,0,0,0.24)',
          }}
        >
          {/* Header */}
          <div
            style={{
              padding: '20px 24px',
              borderBottom: '1px solid var(--stroke-hairline)',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'space-between',
            }}
          >
            <div className="font-display" style={{ fontSize: '18px', color: 'var(--ink-primary)', letterSpacing: '-0.005em' }}>
              {t('mvp.dash.addr_add.title')}
            </div>
            <button
              type="button"
              onClick={onClose}
              aria-label="Close"
              style={{
                background: 'transparent',
                border: 'none',
                color: 'var(--ink-tertiary)',
                fontSize: '22px',
                cursor: 'pointer',
                padding: '0 4px',
                lineHeight: 1,
              }}
            >
              ×
            </button>
          </div>

          {/* Body */}
          <div style={{ padding: '20px 24px 24px' }}>
            {mode === 'choose' && (
              <ChooseMethod t={t} onPick={(m) => setMode(m)} />
            )}

            {mode === 'scan' && (
              <ScanArea
                t={t}
                onBack={handleBack}
                onDecode={handleScanDecoded}
              />
            )}

            {mode === 'paste' && (
              <PasteArea
                t={t}
                value={pasteInput}
                onChange={setPasteInput}
                valid={pasteValid}
                touched={pasteInput.length > 0}
                onBack={handleBack}
                onContinue={handlePasteContinue}
              />
            )}

            {mode === 'confirm' && (
              <ConfirmArea
                t={t}
                address={address}
                label={label}
                onLabelChange={setLabel}
                setAsDefault={setAsDefault}
                onSetAsDefaultChange={setSetAsDefault}
                walletCount={wallets.length}
                duplicate={duplicate}
                onBack={handleBack}
                onCancel={onClose}
                onSave={handleSave}
              />
            )}
          </div>
        </div>
      </div>
    );
  }

  function ChooseMethod({ t, onPick }) {
    return (
      <>
        <div className="font-body" style={{ fontSize: '13px', color: 'var(--ink-secondary)', marginBottom: '16px' }}>
          {t('mvp.dash.addr_add.instruction')}
        </div>
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '12px' }}>
          <MethodButton
            icon="📷"
            title={t('mvp.dash.addr_add.method_scan')}
            desc={t('mvp.dash.addr_add.method_scan_desc')}
            onClick={() => onPick('scan')}
          />
          <MethodButton
            icon="⌨️"
            title={t('mvp.dash.addr_add.method_paste')}
            desc={t('mvp.dash.addr_add.method_paste_desc')}
            onClick={() => onPick('paste')}
          />
        </div>
      </>
    );
  }

  function MethodButton({ icon, title, desc, onClick }) {
    return (
      <button
        type="button"
        onClick={onClick}
        className="font-body"
        style={{
          padding: '16px 14px',
          background: 'var(--surface-paper)',
          border: '1px solid var(--stroke-rule)',
          cursor: 'pointer',
          textAlign: 'left',
          display: 'flex',
          flexDirection: 'column',
          gap: '6px',
          minHeight: '92px',
        }}
      >
        <div style={{ fontSize: '20px', lineHeight: 1 }}>{icon}</div>
        <div style={{ fontSize: '14px', fontWeight: 500, color: 'var(--ink-primary)' }}>{title}</div>
        <div style={{ fontSize: '11px', color: 'var(--ink-tertiary)', lineHeight: 1.4 }}>{desc}</div>
      </button>
    );
  }

  function ScanArea({ t, onBack, onDecode }) {
    return (
      <>
        <BackLink t={t} onBack={onBack} />
        <window.QRScanner onDecode={onDecode} />
      </>
    );
  }

  function PasteArea({ t, value, onChange, valid, touched, onBack, onContinue }) {
    const handleKeyDown = (e) => {
      if (e.key === 'Enter' && valid) onContinue();
    };
    return (
      <>
        <BackLink t={t} onBack={onBack} />
        <div className="smallcaps-mono" style={{ color: 'var(--ink-secondary)', marginBottom: '6px' }}>
          {t('mvp.dash.addr_add.paste_label')}
        </div>
        <input
          type="text"
          autoFocus
          value={value}
          onChange={(e) => onChange(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder={t('mvp.dash.addr_add.paste_placeholder')}
          className="font-mono"
          style={{
            width: '100%',
            padding: '10px 14px',
            border: `1px solid ${touched && !valid ? 'var(--status-negative)' : 'var(--stroke-rule)'}`,
            background: 'var(--surface-paper)',
            fontSize: '14px',
            color: 'var(--ink-primary)',
            outline: 'none',
          }}
        />
        {touched && !valid && (
          <div className="font-body" style={{ marginTop: '6px', fontSize: '11px', color: 'var(--status-negative)' }}>
            {t('mvp.dash.addr_add.paste_invalid')}
          </div>
        )}
        <div style={{ marginTop: '18px', display: 'flex', justifyContent: 'flex-end' }}>
          <button
            type="button"
            onClick={onContinue}
            disabled={!valid}
            className="font-body"
            style={{
              padding: '10px 20px',
              background: valid ? 'var(--ink-primary)' : 'var(--stroke-hairline)',
              border: `1px solid ${valid ? 'var(--ink-primary)' : 'var(--stroke-rule)'}`,
              color: valid ? 'var(--ink-inverse)' : 'var(--ink-tertiary)',
              fontSize: '13px',
              cursor: valid ? 'pointer' : 'not-allowed',
              letterSpacing: '0.02em',
            }}
          >
            {t('mvp.dash.addr_add.continue')} →
          </button>
        </div>
      </>
    );
  }

  function ConfirmArea({
    t, address, label, onLabelChange, setAsDefault, onSetAsDefaultChange,
    walletCount, duplicate, onBack, onCancel, onSave,
  }) {
    const canSave = !duplicate;
    const placeholder = `Wallet ${walletCount + 1}`;
    return (
      <>
        <BackLink t={t} onBack={onBack} />

        <div className="smallcaps-mono" style={{ color: 'var(--ink-secondary)', marginBottom: '6px' }}>
          {t('mvp.dash.addr_add.captured_label')}
        </div>
        <div
          className="font-mono"
          style={{
            padding: '10px 14px',
            border: '1px solid var(--stroke-hairline)',
            background: duplicate ? 'rgba(122, 38, 32, 0.06)' : 'var(--surface-paper)',
            fontSize: '13px',
            color: 'var(--ink-primary)',
            wordBreak: 'break-all',
          }}
        >
          {address}
        </div>
        {duplicate && (
          <div className="font-body" style={{ marginTop: '6px', fontSize: '11px', color: 'var(--status-negative)' }}>
            {t('mvp.dash.addr_add.duplicate')}
          </div>
        )}

        {/* Chain selector skeleton: only Base is enabled in MVP, but the
            other chains are drawn so the user understands what's coming. */}
        <div style={{ marginTop: '16px' }}>
          <div className="smallcaps-mono" style={{ color: 'var(--ink-secondary)', marginBottom: '6px' }}>
            {t('mvp.dash.addr_add.chain_label')}
          </div>
          <div style={{ display: 'flex', gap: '6px', flexWrap: 'wrap' }}>
            <ChainOption label="Base"      selected />
            <ChainOption label="Arbitrum"  disabled />
            <ChainOption label="Optimism"  disabled />
            <ChainOption label="Polygon"   disabled />
            <ChainOption label="Solana"    disabled />
            <ChainOption label="Ethereum"  disabled />
          </div>
          <div className="font-body" style={{ marginTop: '8px', fontSize: '11px', color: 'var(--ink-tertiary)', lineHeight: 1.5 }}>
            {t('mvp.dash.addr_add.chain_disclaimer')}
          </div>
        </div>

        <div style={{ marginTop: '16px' }}>
          <div className="smallcaps-mono" style={{ color: 'var(--ink-secondary)', marginBottom: '6px' }}>
            {t('mvp.dash.addr_add.label_label')}
          </div>
          <input
            type="text"
            value={label}
            onChange={(e) => onLabelChange(e.target.value)}
            placeholder={placeholder}
            className="font-body"
            style={{
              width: '100%',
              padding: '10px 14px',
              border: '1px solid var(--stroke-rule)',
              background: 'var(--surface-paper)',
              fontSize: '14px',
              color: 'var(--ink-primary)',
              outline: 'none',
            }}
          />
        </div>

        <label
          className="font-body"
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: '8px',
            marginTop: '14px',
            fontSize: '13px',
            color: 'var(--ink-secondary)',
            cursor: 'pointer',
          }}
        >
          <input
            type="checkbox"
            checked={setAsDefault}
            onChange={(e) => onSetAsDefaultChange(e.target.checked)}
            style={{ width: '16px', height: '16px', cursor: 'pointer' }}
          />
          {t('mvp.dash.addr_add.default_label')}
        </label>

        <div style={{ marginTop: '22px', display: 'flex', justifyContent: 'flex-end', gap: '10px' }}>
          <button
            type="button"
            onClick={onCancel}
            className="font-body"
            style={{
              padding: '10px 18px',
              background: 'transparent',
              border: '1px solid var(--stroke-rule)',
              color: 'var(--ink-primary)',
              fontSize: '13px',
              cursor: 'pointer',
              letterSpacing: '0.02em',
            }}
          >
            {t('mvp.dash.addr_add.cancel')}
          </button>
          <button
            type="button"
            onClick={onSave}
            disabled={!canSave}
            className="font-body"
            style={{
              padding: '10px 20px',
              background: canSave ? 'var(--ink-primary)' : 'var(--stroke-hairline)',
              border: `1px solid ${canSave ? 'var(--ink-primary)' : 'var(--stroke-rule)'}`,
              color: canSave ? 'var(--ink-inverse)' : 'var(--ink-tertiary)',
              fontSize: '13px',
              cursor: canSave ? 'pointer' : 'not-allowed',
              letterSpacing: '0.02em',
            }}
          >
            {t('mvp.dash.addr_add.save')}
          </button>
        </div>
      </>
    );
  }

  // Chain skeleton chip — only Base is selectable in MVP; other chains render
  // as muted "phase 2" labels so the multi-chain story is visible to users.
  function ChainOption({ label, selected, disabled }) {
    return (
      <button
        type="button"
        disabled={disabled}
        className="font-body"
        style={{
          padding: '6px 10px',
          fontSize: '12px',
          letterSpacing: '0.02em',
          background: selected ? 'var(--ink-primary)' : 'var(--surface-paper)',
          border: `1px solid ${selected ? 'var(--ink-primary)' : 'var(--stroke-rule)'}`,
          color: selected ? 'var(--ink-inverse)' : (disabled ? 'var(--ink-tertiary)' : 'var(--ink-primary)'),
          cursor: disabled ? 'not-allowed' : (selected ? 'default' : 'pointer'),
          opacity: disabled ? 0.55 : 1,
        }}
        title={disabled ? 'Coming via Circle CCTP — phase 2' : undefined}
      >
        {label}
        {disabled && (
          <span style={{ marginLeft: '6px', fontSize: '9px', letterSpacing: '0.06em', color: 'var(--ink-tertiary)' }}>
            soon
          </span>
        )}
      </button>
    );
  }

  function BackLink({ t, onBack }) {
    return (
      <button
        type="button"
        onClick={onBack}
        className="font-body"
        style={{
          background: 'transparent',
          border: 'none',
          padding: 0,
          marginBottom: '14px',
          cursor: 'pointer',
          fontSize: '11px',
          color: 'var(--ink-tertiary)',
          letterSpacing: '0.04em',
        }}
      >
        {t('mvp.dash.addr_add.back')}
      </button>
    );
  }

  window.AddAddressModal = AddAddressModal;
})();
