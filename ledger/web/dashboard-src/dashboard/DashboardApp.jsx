// MVP dashboard · top-level routing.
//
// Onboarding flow:
//   1. MvpGithubAuthScreen — prototype GitHub-style consent, email-scoped
//   2. MvpClaimScreen      — claim a wallet assigned to the signed-in email
//   3. DashboardSurface   — chrome + 5 tabs
//
// State comes from MvpAppStateProvider (MVP-specific localStorage keys, so
// MVP dashboard state is fully isolated from the main project's dashboard).

(function () {
  function DashboardRouter() {
    const { authChecked, registered, claimed } = window.useAppState();
    if (!authChecked) return <AuthCheckingScreen />;
    if (!registered) return <window.MvpGithubAuthScreen />;
    if (!claimed)    return <window.MvpClaimScreen />;
    return <DashboardSurface />;
  }

  function AuthCheckingScreen() {
    const t = window.useT();
    return (
      <div
        style={{
          minHeight: '100vh',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          padding: '48px',
        }}
      >
        <div className="smallcaps-mono" style={{ color: 'var(--ink-tertiary)' }}>
          {t('mvp.dash.auth.checking')}
        </div>
      </div>
    );
  }

  function DashboardSurface() {
    const { currentUser, ownerEmail, mockState, agents: claimedAgents, activeAgentId, setActiveAgent, claimToken, deepLinkAgentId } =
      window.useAppState();
    // ?tab=<id> lets dev/test land on a specific tab. Default portfolio.
    // ?onramp=1 opens the Coinbase onramp modal on mount (screenshot helper).
    const params = React.useMemo(() => new URLSearchParams(window.location.search), []);
    const initialTab = React.useMemo(() => {
      const t = params.get('tab');
      return ['portfolio', 'overview', 'transactions', 'funding', 'settings'].includes(t)
        ? t
        : 'portfolio';
    }, [params]);
    const [activeTab, setActiveTab] = React.useState(initialTab);
    React.useEffect(() => {
      if (params.get('onramp') === '1' && activeTab === 'funding') {
        // Poll up to ~2s for the button to mount, then click it.
        let tries = 0;
        const tick = () => {
          tries += 1;
          const btn = Array.from(document.querySelectorAll('button')).find(
            (b) => /Open Coinbase/i.test(b.textContent || ''),
          );
          if (btn) { btn.click(); return; }
          if (tries < 20) setTimeout(tick, 100);
        };
        setTimeout(tick, 100);
      }
      // eslint-disable-next-line react-hooks/exhaustive-deps
    }, []);
    const [addAgentOpen, setAddAgentOpen] = React.useState(() => !!(claimToken && deepLinkAgentId));
    const [ledgerDashboardState, setLedgerDashboardState] = React.useState(null);

    React.useEffect(() => {
      let cancelled = false;
      fetch('/dashboard/data')
        .then((response) => {
          if (!response.ok) throw new Error(`dashboard data ${response.status}`);
          return response.json();
        })
        .then((payload) => {
          if (cancelled || !payload || !payload.agents || !payload.defaultAgentId) return;
          window.DASH_MOCK.day1 = payload;
          window.DASH_CLAIMABLE = {};
          setLedgerDashboardState(payload);
        })
        .catch(() => {
          // Keep the standalone day1 fixture when the backend is unavailable.
        });
      return () => { cancelled = true; };
    }, [ownerEmail]);

    // Same roster-merging logic as main: base roster from mockState ∪
    // user-claimed pool agents. Pool agents always appear as fresh-claim
    // payloads regardless of mockState (matches "you just claimed this").
    const fullState = ledgerDashboardState || window.DASH_MOCK[mockState] || window.DASH_MOCK.day1;
    const baseRoster = fullState.agents;
    const claimablePool = window.DASH_CLAIMABLE || {};

    const claimedFromPool = {};
    for (const id of claimedAgents) {
      if (!baseRoster[id] && claimablePool[id]) {
        claimedFromPool[id] = claimablePool[id];
      }
    }
    const agentRoster = { ...baseRoster, ...claimedFromPool };

    const displayedAgents = claimedAgents.filter((id) => agentRoster[id]);

    const fallbackId = activeAgentId && agentRoster[activeAgentId]
      ? activeAgentId
      : fullState.defaultAgentId;
    const data = agentRoster[fallbackId] || agentRoster[fullState.defaultAgentId];

    // Auto-snap active agent when mockState's roster doesn't include it.
    React.useEffect(() => {
      if (activeAgentId && !agentRoster[activeAgentId]) {
        setActiveAgent(fullState.defaultAgentId);
      }
    }, [mockState, activeAgentId, agentRoster, fullState.defaultAgentId, setActiveAgent]);

    // Force-remount views on agent switch so their local state (Funding form
    // drafts, etc.) resets to the incoming agent.
    const viewKey = fallbackId;

    const handleSelectAgent = (id) => {
      setActiveAgent(id);
      setActiveTab('overview');
    };

    let view;
    if (activeTab === 'portfolio') {
      view = (
        <window.MvpPortfolioView
          effectiveRoster={agentRoster}
          displayedAgents={displayedAgents}
          onSelectAgent={handleSelectAgent}
          onAddAgent={() => setAddAgentOpen(true)}
        />
      );
    } else if (activeTab === 'overview') {
      view = (
        <window.MvpOverviewView
          key={viewKey}
          data={data}
          ownerEmail={ownerEmail}
          onJumpTo={setActiveTab}
        />
      );
    } else if (activeTab === 'transactions') {
      view = <window.MvpTransactionsView key={viewKey} data={data} />;
    } else if (activeTab === 'funding') {
      view = <window.MvpFundingView key={viewKey} data={data} />;
    } else if (activeTab === 'settings') {
      view = <window.MvpSettingsView key={viewKey} data={data} />;
    }

    return (
      <>
        <window.MvpDashboardChrome
          activeTab={activeTab}
          setActiveTab={setActiveTab}
          user={currentUser}
          ownerEmail={ownerEmail}
          agentList={displayedAgents}
          agentRoster={agentRoster}
          onAddAgent={() => setAddAgentOpen(true)}
        >
          {view}
        </window.MvpDashboardChrome>
        {addAgentOpen && (
          <window.MvpAddAgentModal onClose={() => setAddAgentOpen(false)} />
        )}
      </>
    );
  }

  function DashboardRoot() {
    return (
      <window.LangProvider>
        <window.AppStateProvider>
          <div style={{ background: 'var(--surface-paper)', minHeight: '100vh', position: 'relative' }}>
            <div
              style={{
                position: 'fixed',
                top: '24px',
                right: '48px',
                zIndex: 50,
                display: 'flex',
                alignItems: 'center',
                gap: '16px',
              }}
            >
              <window.LangSwitcher />
            </div>
            <DashboardRouter />
          </div>
        </window.AppStateProvider>
      </window.LangProvider>
    );
  }

  const root = ReactDOM.createRoot(document.getElementById('root'));
  root.render(<DashboardRoot />);
})();
