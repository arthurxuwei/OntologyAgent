// MVP · AddAgentModal — modal wrapper around ClaimForm for adding another
// agent after the initial claim. Replaces the main project's AddAgentModal
// (which used a non-interactive token + click-an-agent flow). The actual
// validate/confirm logic lives in ClaimForm, shared with ClaimScreen.
//
// Opened from AgentSwitcher's "+ Add agent" CTA; mounted by DashboardApp.

(function () {
  function MvpAddAgentModal({ onClose }) {
    const t = window.useT();

    React.useEffect(() => {
      const onKey = (e) => { if (e.key === 'Escape') onClose && onClose(); };
      document.addEventListener('keydown', onKey);
      return () => document.removeEventListener('keydown', onKey);
    }, [onClose]);

    const handleClaimed = () => {
      if (onClose) onClose();
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
            width: '520px',
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
              {t('mvp.dash.claim.add_title')}
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
            <window.ClaimForm mode="add" onClaimed={handleClaimed} onDismiss={onClose} />
          </div>
        </div>
      </div>
    );
  }

  window.MvpAddAgentModal = MvpAddAgentModal;
})();
