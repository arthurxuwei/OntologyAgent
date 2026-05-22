// MVP DashboardChrome — same shape as the main project's chrome (top bar +
// horizontal nav row + content), but with **5 tabs only**:
//
//   portfolio · overview · transactions · funding · settings
//
// Cut from main:
//   - credit          : MVP has no credit story
//   - reconciliation  : MVP doesn't expose the Circle delegation ledger
//
// MockStateToggle still sits on the right edge of the nav row, AgentSwitcher
// sits in the top bar — both are reused from main as-is (../src/dashboard/).

(function () {
  function MvpDashboardChrome({ activeTab, setActiveTab, ownerEmail, agentList, agentRoster, onAddAgent, children }) {
    const t = window.useT();
    const { activeAgentId, setActiveAgent } = window.useAppState();
    const agents = agentList || [];

    const tabs = [
      { id: 'portfolio',    label: t('mvp.dash.nav.portfolio') },
      { id: 'overview',     label: t('mvp.dash.nav.overview') },
      { id: 'transactions', label: t('mvp.dash.nav.transactions') },
      { id: 'funding',      label: t('mvp.dash.nav.funding') },
      { id: 'settings',     label: t('mvp.dash.nav.settings') },
    ];

    return (
      // paddingTop reserves space for the fixed top bar (64px) + 16px of
      // breathing room. Main project uses 128px which leaves a big empty
      // band above the nav row — tighter feels more product-like.
      <div className="w-full" style={{ paddingTop: '80px' }}>
        {/* Top bar */}
        <div
          className="fixed left-0 right-0 flex items-center"
          style={{
            top: 0,
            height: '64px',
            paddingLeft: '64px',
            paddingRight: '160px',
            background: 'var(--surface-paper)',
            borderBottom: '1px solid var(--stroke-hairline)',
            zIndex: 30,
          }}
        >
          <div className="font-display" style={{ fontSize: '24px', fontWeight: 400, letterSpacing: '-0.01em' }}>
            Chief
          </div>
          <span
            className="font-mono"
            style={{
              marginLeft: '10px',
              fontSize: '10px',
              letterSpacing: '0.1em',
              color: 'var(--ink-tertiary)',
            }}
          >
            MVP
          </span>
          <div className="flex-1" />
          {agents && agents.length > 0 && (
            <div className="flex items-center" style={{ gap: '12px' }}>
              <window.AgentSwitcher
                agents={agents}
                activeAgentId={activeAgentId}
                agentRoster={agentRoster || {}}
                onSelect={setActiveAgent}
                onAddAgent={onAddAgent}
              />
              <span className="font-mono" style={{ fontSize: '12px', color: 'var(--ink-tertiary)' }}>
                {ownerEmail || 'unclaimed'}
              </span>
            </div>
          )}
        </div>

        {/* Nav row */}
        <div className="px-16" style={{ marginTop: '8px' }}>
          <div className="flex items-baseline justify-between" style={{ gap: '32px', flexWrap: 'wrap' }}>
            <div className="flex" style={{ gap: '32px' }}>
              {tabs.map((tab) => {
                const active = tab.id === activeTab;
                return (
                  <button
                    key={tab.id}
                    type="button"
                    onClick={() => setActiveTab(tab.id)}
                    className="smallcaps-mono"
                    style={{
                      background: 'transparent',
                      border: 'none',
                      padding: '4px 0 8px',
                      cursor: 'pointer',
                      color: active ? 'var(--ink-primary)' : 'var(--ink-tertiary)',
                      borderBottom: active ? '2px solid var(--accent-amber)' : '2px solid transparent',
                      letterSpacing: '0.08em',
                      transition: 'color 120ms, border-color 120ms',
                    }}
                    aria-pressed={active}
                  >
                    {tab.label}
                  </button>
                );
              })}
            </div>
          </div>
          <div style={{ height: '1px', background: 'var(--stroke-hairline)', marginTop: '4px' }} />
        </div>

        {/* Content */}
        <div className="px-16 pt-8 pb-24">{children}</div>
      </div>
    );
  }

  window.MvpDashboardChrome = MvpDashboardChrome;
})();
