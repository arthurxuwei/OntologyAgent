// AgentSwitcher — clickable badge in DashboardChrome's top bar that opens
// a dropdown listing every claimed agent + an "+ Add agent" CTA.
//
// Replaces the static `agentA` badge from batches A–D. Closed state mirrors
// that badge visually (mono pill with the active agent's id) so existing
// screenshots stay coherent; the only addition is a small ▾ caret.
//
// Open state lists each agent with name · role · current balance, and the
// active row gets an amber left border. Click a row → setActiveAgent + close.
// Click "+ Add agent" → onAddAgent() prop (parent decides what flow to run).
//
// Accepts agentRoster ({id → metadata}) so it can render rich rows; falls
// back gracefully if a claimed id is missing from the roster (rare — only if
// localStorage holds an id absent from the current mockState's roster).

function AgentSwitcher({ agents, activeAgentId, agentRoster, onSelect, onAddAgent }) {
  const t = window.useT();
  const [open, setOpen] = React.useState(false);
  const ref = React.useRef(null);

  React.useEffect(() => {
    if (!open) return;
    const onDocClick = (e) => {
      if (ref.current && !ref.current.contains(e.target)) setOpen(false);
    };
    const onKey = (e) => {
      if (e.key === 'Escape') setOpen(false);
    };
    document.addEventListener('mousedown', onDocClick);
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('mousedown', onDocClick);
      document.removeEventListener('keydown', onKey);
    };
  }, [open]);

  if (!agents || agents.length === 0) return null;

  const activeMeta = agentRoster[activeAgentId];
  const activeLabel = activeMeta && activeMeta.agent && activeMeta.agent.name
    ? activeMeta.agent.name
    : (activeAgentId || agents[0]);

  return (
    <div ref={ref} style={{ position: 'relative' }}>
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="font-mono"
        style={{
          fontSize: '11px',
          padding: '3px 10px 3px 8px',
          border: '1px solid var(--stroke-rule)',
          color: 'var(--ink-primary)',
          letterSpacing: '0.04em',
          background: 'transparent',
          cursor: 'pointer',
          display: 'inline-flex',
          alignItems: 'center',
          gap: '6px',
        }}
        aria-haspopup="menu"
        aria-expanded={open}
      >
        <span style={{ maxWidth: '220px', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
          {activeLabel}
        </span>
        <span style={{ color: 'var(--ink-tertiary)', fontSize: '10px' }}>▾</span>
      </button>

      {open && (
        <div
          role="menu"
          style={{
            position: 'absolute',
            top: 'calc(100% + 8px)',
            right: 0,
            minWidth: '320px',
            background: 'var(--surface-paper)',
            border: '1px solid var(--stroke-rule)',
            boxShadow: '0 8px 32px rgba(26,26,26,0.12)',
            zIndex: 60,
          }}
        >
          <div
            className="smallcaps-mono"
            style={{
              padding: '12px 16px 8px',
              color: 'var(--ink-tertiary)',
              fontSize: '10px',
              borderBottom: '1px solid var(--stroke-hairline)',
            }}
          >
            {t('dash.switcher.title')} · {agents.length}
          </div>

          {agents.map((id) => {
            const meta = agentRoster[id];
            const active = id === activeAgentId;
            return (
              <button
                key={id}
                type="button"
                onClick={() => {
                  onSelect(id);
                  setOpen(false);
                }}
                className="font-body"
                style={{
                  width: '100%',
                  background: active ? 'var(--accent-amber-soft)' : 'transparent',
                  border: 'none',
                  borderLeft: active
                    ? '3px solid var(--accent-amber)'
                    : '3px solid transparent',
                  padding: '12px 16px',
                  cursor: 'pointer',
                  textAlign: 'left',
                  display: 'block',
                  borderBottom: '1px solid var(--stroke-hairline)',
                }}
              >
                <AgentRow meta={meta} fallbackId={id} active={active} t={t} />
              </button>
            );
          })}

          <button
            type="button"
            onClick={() => {
              setOpen(false);
              onAddAgent && onAddAgent();
            }}
            className="font-body"
            style={{
              width: '100%',
              background: 'transparent',
              border: 'none',
              padding: '14px 16px',
              cursor: 'pointer',
              textAlign: 'left',
              fontSize: '13px',
              color: 'var(--accent-amber)',
              letterSpacing: '0.02em',
            }}
          >
            + {t('dash.switcher.add')}
          </button>
        </div>
      )}
    </div>
  );
}

function AgentRow({ meta, fallbackId, active, t }) {
  if (!meta) {
    // Defensive fallback — claimed id without a roster entry. Should be rare
    // (e.g. user claimed in mature, then toggled to empty/day1 where only
    // agentA exists). Show id only; balance/role omitted.
    return (
      <div>
        <div className="font-mono" style={{ fontSize: '13px', color: 'var(--ink-primary)' }}>
          {fallbackId}
        </div>
        <div
          className="font-body"
          style={{ fontSize: '11px', color: 'var(--ink-tertiary)', marginTop: '2px' }}
        >
          {t('dash.switcher.no_data_in_state')}
        </div>
      </div>
    );
  }
  return (
    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', gap: '12px' }}>
      <div style={{ minWidth: 0, flex: 1 }}>
        <div
          className="font-mono"
          style={{
            fontSize: '13px',
            color: active ? 'var(--accent-amber)' : 'var(--ink-primary)',
            letterSpacing: '0.02em',
          }}
        >
          {meta.agent.name}
        </div>
        <div
          className="font-body"
          style={{
            fontSize: '11px',
            color: 'var(--ink-tertiary)',
            marginTop: '2px',
            whiteSpace: 'nowrap',
            overflow: 'hidden',
            textOverflow: 'ellipsis',
          }}
        >
          {meta.agent.role}
        </div>
      </div>
      <div
        className="font-mono"
        style={{
          fontSize: '13px',
          color: 'var(--ink-primary)',
          whiteSpace: 'nowrap',
          letterSpacing: '-0.01em',
        }}
      >
        {window.formatAmount(meta.balance.available)}
      </div>
    </div>
  );
}

window.AgentSwitcher = AgentSwitcher;
