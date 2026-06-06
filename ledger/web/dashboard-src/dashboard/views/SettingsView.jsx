// MVP Settings — three sections:
//   1. Account         : owner email (read-only).
//   2. External wallets: owner's address book (Step 5 addition). Same
//                        wallets[] the AddressPicker / AddAddressModal read.
//                        Each row supports inline label edit, set-default,
//                        and delete. "+ Add wallet" opens AddAddressModal.
//   3. Spend limits    : per-trade cap, editable, per-agent localStorage.
//
// Cut from main's Settings: Approval section, reset controls, and demo links.

(function () {
  const CAPS_STORAGE_KEY = 'kovaloop_mvp_dash_caps';

  function readCaps() {
    try {
      const raw = window.localStorage.getItem(CAPS_STORAGE_KEY);
      if (!raw) return {};
      const parsed = JSON.parse(raw);
      return typeof parsed === 'object' && parsed !== null ? parsed : {};
    } catch { return {}; }
  }

  function writeCap(agentId, value) {
    const caps = readCaps();
    caps[agentId] = value;
    window.localStorage.setItem(CAPS_STORAGE_KEY, JSON.stringify(caps));
  }

  function MvpSettingsView({ data }) {
    const t = window.useT();
    const {
      currentUser, signOut,
      wallets, defaultWalletId,
      removeWallet, setDefaultWallet, updateWalletLabel,
    } = window.useAppState();
    const [addOpen, setAddOpen] = React.useState(false);
    if (!data) return null;

    // Effective cap: localStorage override (per agent) wins over mock seed.
    const agentId = data.agent.id;
    const seedCap = data.settings && data.settings.limits && data.settings.limits.perTradeCap;
    const initialCap = readCaps()[agentId] ?? seedCap;

    return (
      <div>
        <div className="smallcaps-mono" style={{ color: 'var(--ink-tertiary)', marginBottom: '24px' }}>
          {t('mvp.dash.settings.slug')} · {data.agent.name}
        </div>

        {/* Section 1 — Account (signed-in identity card) */}
        <Section title={t('mvp.dash.settings.account_title')}>
          <SignedInCard t={t} user={currentUser} onSignOut={signOut} />
        </Section>

        {/* Section 2 — External wallets (address book) */}
        <Section title={t('mvp.dash.settings.wallets_title')}
                 description={t('mvp.dash.settings.wallets_description')}>
          <WalletList
            t={t}
            wallets={wallets}
            defaultWalletId={defaultWalletId}
            onSetDefault={setDefaultWallet}
            onRemove={removeWallet}
            onUpdateLabel={updateWalletLabel}
            onAdd={() => setAddOpen(true)}
          />
        </Section>

        {/* Section 3 — Spend limits (editable, per agent) */}
        <Section title={t('mvp.dash.settings.limits_title')}>
          <PerTradeCapEditor
            t={t}
            agentId={agentId}
            initialValue={initialCap}
          />
        </Section>

        {/* Add-wallet modal — shared with FundingView. Both opens write to
            the same wallets[] via useAppState; the list above re-renders
            from context on save. */}
        <window.AddAddressModal
          open={addOpen}
          onClose={() => setAddOpen(false)}
        />
      </div>
    );
  }

  function WalletList({ t, wallets, defaultWalletId, onSetDefault, onRemove, onUpdateLabel, onAdd }) {
    if (wallets.length === 0) {
      return (
        <div style={{ maxWidth: '520px' }}>
          <div className="font-body" style={{ fontSize: '13px', color: 'var(--ink-tertiary)', marginBottom: '12px' }}>
            {t('mvp.dash.settings.wallets_empty')}
          </div>
          <button
            type="button"
            onClick={onAdd}
            className="font-body"
            style={{
              padding: '8px 16px',
              background: 'var(--ink-primary)',
              border: '1px solid var(--ink-primary)',
              color: 'var(--ink-inverse)',
              fontSize: '13px',
              cursor: 'pointer',
              letterSpacing: '0.02em',
            }}
          >
            + {t('mvp.dash.settings.wallets_add_first')}
          </button>
        </div>
      );
    }
    return (
      <div style={{ maxWidth: '640px' }}>
        <div style={{ border: '1px solid var(--stroke-rule)', background: 'var(--surface-paper)' }}>
          {wallets.map((w, i) => (
            <WalletRow
              key={w.id}
              t={t}
              wallet={w}
              isDefault={w.id === defaultWalletId}
              canRemove={true}
              isLast={i === wallets.length - 1}
              onSetDefault={() => onSetDefault(w.id)}
              onRemove={() => {
                if (confirm(t('mvp.dash.settings.wallets_remove_confirm').replace('{label}', w.label))) {
                  onRemove(w.id);
                }
              }}
              onUpdateLabel={(newLabel) => onUpdateLabel(w.id, newLabel)}
            />
          ))}
        </div>
        <button
          type="button"
          onClick={onAdd}
          className="font-body"
          style={{
            marginTop: '12px',
            padding: '8px 14px',
            background: 'transparent',
            border: '1px dashed var(--stroke-rule)',
            color: 'var(--ink-primary)',
            fontSize: '13px',
            cursor: 'pointer',
            letterSpacing: '0.02em',
          }}
        >
          + {t('mvp.dash.settings.wallets_add')}
        </button>
      </div>
    );
  }

  function WalletRow({ t, wallet, isDefault, isLast, onSetDefault, onRemove, onUpdateLabel }) {
    const [editing, setEditing] = React.useState(false);
    const [draftLabel, setDraftLabel] = React.useState(wallet.label);
    // Suppresses the saveEdit that React fires via the input's onBlur when
    // editing flips to false (the input unmounts → React fires blur with the
    // pre-cancel closure, which would commit the unwanted draft).
    const skipNextBlurRef = React.useRef(false);

    React.useEffect(() => { setDraftLabel(wallet.label); }, [wallet.label]);

    const startEdit = () => {
      skipNextBlurRef.current = false;
      setDraftLabel(wallet.label);
      setEditing(true);
    };
    const cancelEdit = () => {
      skipNextBlurRef.current = true;
      setDraftLabel(wallet.label);
      setEditing(false);
    };
    const saveEdit = () => {
      if (skipNextBlurRef.current) {
        skipNextBlurRef.current = false;
        return;
      }
      const trimmed = draftLabel.trim();
      if (trimmed && trimmed !== wallet.label) onUpdateLabel(trimmed);
      setEditing(false);
    };

    return (
      <div
        style={{
          padding: '14px 16px',
          borderBottom: isLast ? 'none' : '1px solid var(--stroke-hairline)',
          display: 'flex',
          alignItems: 'center',
          gap: '14px',
        }}
      >
        <div style={{ minWidth: 0, flex: 1 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: '8px', marginBottom: '4px' }}>
            {isDefault && (
              <span style={{ color: 'var(--accent-amber)', fontSize: '13px' }}>★</span>
            )}
            {editing ? (
              <input
                type="text"
                value={draftLabel}
                autoFocus
                onChange={(e) => setDraftLabel(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter') saveEdit();
                  if (e.key === 'Escape') cancelEdit();
                }}
                onBlur={saveEdit}
                className="font-body"
                style={{
                  padding: '4px 8px',
                  border: '1px solid var(--stroke-rule)',
                  background: 'var(--surface-paper)',
                  fontSize: '13px',
                  fontWeight: 500,
                  color: 'var(--ink-primary)',
                  outline: 'none',
                  width: '180px',
                }}
              />
            ) : (
              <span className="font-body" style={{ fontSize: '13px', fontWeight: 500, color: 'var(--ink-primary)' }}>
                {wallet.label}
              </span>
            )}
            <span
              className="smallcaps-mono"
              style={{
                fontSize: '9px',
                letterSpacing: '0.06em',
                color: 'var(--ink-secondary)',
                border: '1px solid var(--stroke-rule)',
                background: 'var(--surface-card)',
                padding: '1px 6px',
              }}
            >
              {(wallet.chain || 'base').toUpperCase()}
            </span>
            {isDefault && (
              <span className="smallcaps-mono" style={{ fontSize: '9px', color: 'var(--accent-amber)', letterSpacing: '0.06em' }}>
                {t('mvp.dash.settings.wallets_default_tag')}
              </span>
            )}
          </div>
          <div
            className="font-mono"
            style={{
              fontSize: '11px',
              color: 'var(--ink-tertiary)',
              overflow: 'hidden',
              textOverflow: 'ellipsis',
              whiteSpace: 'nowrap',
            }}
            title={wallet.address}
          >
            {wallet.address}
          </div>
        </div>
        <div style={{ display: 'flex', gap: '6px', flexShrink: 0 }}>
          {!editing && (
            <RowAction onClick={startEdit}>{t('mvp.dash.settings.wallets_edit')}</RowAction>
          )}
          {!isDefault && !editing && (
            <RowAction onClick={onSetDefault}>{t('mvp.dash.settings.wallets_set_default')}</RowAction>
          )}
          {!editing && (
            <RowAction onClick={onRemove} negative>{t('mvp.dash.settings.wallets_remove')}</RowAction>
          )}
        </div>
      </div>
    );
  }

  function RowAction({ children, onClick, negative }) {
    return (
      <button
        type="button"
        onClick={onClick}
        className="smallcaps-mono"
        style={{
          padding: '4px 8px',
          background: 'transparent',
          border: '1px solid var(--stroke-rule)',
          color: negative ? 'var(--status-negative)' : 'var(--ink-secondary)',
          fontSize: '9px',
          letterSpacing: '0.06em',
          cursor: 'pointer',
          textTransform: 'uppercase',
        }}
      >
        {children}
      </button>
    );
  }

  function PerTradeCapEditor({ t, agentId, initialValue }) {
    const [value, setValue] = React.useState(String(initialValue));
    const [saved, setSaved] = React.useState(initialValue);
    const [justSaved, setJustSaved] = React.useState(false);

    // When agent switches (key remount), state resets via initialValue.
    const numeric = parseFloat(value);
    const valid = !Number.isNaN(numeric) && numeric > 0 && numeric <= 100;
    const dirty = String(saved) !== value.trim();

    const handleSave = () => {
      if (!valid || !dirty) return;
      writeCap(agentId, numeric);
      setSaved(numeric);
      setJustSaved(true);
      setTimeout(() => setJustSaved(false), 1800);
    };

    const handleReset = () => {
      // Reset back to the last-saved value (cancels current edit).
      setValue(String(saved));
    };

    return (
      <div style={{ maxWidth: '520px' }}>
        <div className="smallcaps-mono" style={{ color: 'var(--ink-secondary)', marginBottom: '6px' }}>
          {t('mvp.dash.settings.limits_per_trade_label')}
        </div>
        <div className="flex items-center" style={{ gap: '8px' }}>
          <input
            type="text"
            value={value}
            onChange={(e) => setValue(e.target.value)}
            onKeyDown={(e) => { if (e.key === 'Enter') handleSave(); }}
            className="font-mono"
            style={{
              width: '140px',
              padding: '10px 14px',
              border: `1px solid ${valid || value === '' ? 'var(--stroke-rule)' : 'var(--status-negative)'}`,
              background: 'var(--surface-paper)',
              fontSize: '14px',
              color: 'var(--ink-primary)',
              outline: 'none',
            }}
          />
          <span className="font-mono" style={{ fontSize: '13px', color: 'var(--ink-tertiary)', letterSpacing: '0.04em' }}>
            USDC
          </span>
          <button
            type="button"
            onClick={handleSave}
            disabled={!valid || !dirty}
            className="font-body"
            style={{
              padding: '8px 16px',
              background: (valid && dirty) ? 'var(--ink-primary)' : 'var(--stroke-hairline)',
              border: `1px solid ${(valid && dirty) ? 'var(--ink-primary)' : 'var(--stroke-rule)'}`,
              color: (valid && dirty) ? 'var(--ink-inverse)' : 'var(--ink-tertiary)',
              fontSize: '12px',
              cursor: (valid && dirty) ? 'pointer' : 'not-allowed',
              letterSpacing: '0.02em',
              marginLeft: '8px',
            }}
          >
            {t('mvp.dash.settings.limits_save')}
          </button>
          {dirty && (
            <button
              type="button"
              onClick={handleReset}
              className="smallcaps-mono"
              style={{
                background: 'transparent',
                border: 'none',
                padding: '2px 4px',
                cursor: 'pointer',
                fontSize: '10px',
                color: 'var(--ink-tertiary)',
              }}
            >
              {t('mvp.dash.settings.limits_cancel')}
            </button>
          )}
          {justSaved && (
            <span
              className="smallcaps-mono fade-up"
              style={{ fontSize: '10px', color: 'var(--status-positive)', marginLeft: '4px' }}
            >
              ✓ {t('mvp.dash.settings.limits_saved')}
            </span>
          )}
        </div>
        <div
          className="font-body"
          style={{ marginTop: '10px', fontSize: '12px', color: 'var(--ink-tertiary)', lineHeight: 1.5 }}
        >
          {valid
            ? t('mvp.dash.settings.limits_help')
            : t('mvp.dash.settings.limits_invalid')}
        </div>
      </div>
    );
  }

  function Section({ title, description, children, amber }) {
    return (
      <section
        style={{
          marginTop: '32px',
          paddingTop: '24px',
          borderTop: `1px solid ${amber ? 'var(--accent-amber-soft)' : 'var(--stroke-rule)'}`,
        }}
      >
        <div className="smallcaps-mono" style={{ color: amber ? 'var(--accent-amber)' : 'var(--ink-tertiary)', marginBottom: '4px' }}>
          {title}
        </div>
        {description && (
          <div className="font-body" style={{ fontSize: '13px', color: 'var(--ink-secondary)', marginBottom: '18px', maxWidth: '640px' }}>
            {description}
          </div>
        )}
        {children}
      </section>
    );
  }

  function SignedInCard({ t, user, onSignOut }) {
    if (!user) {
      return (
        <div className="font-body" style={{ fontSize: '13px', color: 'var(--ink-tertiary)' }}>
          —
        </div>
      );
    }
    const displayName = user.name || user.login || user.email || '—';
    const handle = user.login ? `@${user.login}` : user.email;
    const providerLabel = providerDisplay(t, user.provider);
    return (
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: '14px',
          padding: '14px 16px',
          border: '1px solid var(--stroke-hairline)',
          background: 'var(--surface-paper)',
          maxWidth: '520px',
        }}
      >
        <IdentityAvatar user={user} size={44} />
        <div style={{ minWidth: 0, flex: 1 }}>
          <div
            className="font-body"
            style={{
              fontSize: '14px',
              fontWeight: 500,
              color: 'var(--ink-primary)',
              lineHeight: 1.3,
            }}
          >
            {displayName}
          </div>
          <div
            className="font-body"
            style={{
              fontSize: '12px',
              color: 'var(--ink-secondary)',
              marginTop: '2px',
              lineHeight: 1.4,
              overflow: 'hidden',
              textOverflow: 'ellipsis',
              whiteSpace: 'nowrap',
            }}
          >
            {handle}
            {handle && user.email && user.login ? (
              <span style={{ color: 'var(--ink-tertiary)' }}> · {user.email}</span>
            ) : null}
          </div>
          <div
            className="smallcaps-mono"
            style={{
              fontSize: '9px',
              color: 'var(--ink-tertiary)',
              letterSpacing: '0.06em',
              marginTop: '6px',
            }}
          >
            {t('mvp.dash.settings.account_signed_in_via')} {providerLabel}
          </div>
        </div>
        {onSignOut && (
          <button
            type="button"
            onClick={onSignOut}
            className="smallcaps-mono"
            style={{
              padding: '6px 12px',
              background: 'transparent',
              border: '1px solid var(--stroke-rule)',
              color: 'var(--ink-secondary)',
              fontSize: '10px',
              letterSpacing: '0.08em',
              cursor: 'pointer',
              textTransform: 'uppercase',
              flexShrink: 0,
            }}
          >
            {t('mvp.dash.settings.account_sign_out')}
          </button>
        )}
      </div>
    );
  }

  function providerDisplay(t, provider) {
    switch (provider) {
      case 'github': return t('mvp.dash.settings.account_provider_github');
      case 'google': return t('mvp.dash.settings.account_provider_google');
      case 'email':  return t('mvp.dash.settings.account_provider_email');
      default:       return provider || '—';
    }
  }

  // CSS-generated identicon — colored circle + initial. No network round-trip
  // (so it works offline and in standalone builds). Color is a deterministic
  // hash of the user's stable id (login → email → name → '?').
  function IdentityAvatar({ user, size = 40 }) {
    if (user && user.avatar_url) {
      return (
        <img
          src={user.avatar_url}
          alt=""
          style={{
            width: size,
            height: size,
            borderRadius: '50%',
            objectFit: 'cover',
            flexShrink: 0,
          }}
        />
      );
    }
    const seed = (user && (user.login || user.email || user.name)) || '?';
    const initial = seed[0].toUpperCase();
    const bg = colorFromString(seed);
    return (
      <div
        style={{
          width: size,
          height: size,
          borderRadius: '50%',
          background: bg,
          color: '#FFFFFF',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          fontWeight: 600,
          fontSize: Math.round(size * 0.42),
          flexShrink: 0,
          fontFamily: '"JetBrains Mono", monospace',
          letterSpacing: '0.02em',
        }}
        aria-hidden="true"
      >
        {initial}
      </div>
    );
  }

  function colorFromString(s) {
    let h = 0;
    for (let i = 0; i < s.length; i++) h = (h * 31 + s.charCodeAt(i)) >>> 0;
    const hue = h % 360;
    return `hsl(${hue} 38% 42%)`;
  }

  window.MvpSettingsView = MvpSettingsView;
})();
