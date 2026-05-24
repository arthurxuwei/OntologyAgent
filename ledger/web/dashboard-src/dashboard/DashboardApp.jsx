// MVP dashboard · top-level routing.
//
// Onboarding flow:
//   1. MvpGithubAuthScreen — prototype GitHub-style consent, email-scoped
//   2. MvpClaimScreen      — claim a wallet by claim code
//   3. DashboardSurface   — chrome + 5 tabs
//
// State comes from MvpAppStateProvider (MVP-specific localStorage keys, so
// MVP dashboard state is fully isolated from the main project's dashboard).

(function () {
  const REQUIRED_DASHBOARD_GLOBALS = [
    'formatAmount',
    'LangProvider',
    'LangSwitcher',
    'AppStateProvider',
    'useAppState',
    'DASH_MOCK',
    'DASH_CLAIMABLE',
    'PendingBalanceLine',
    'TransactionRow',
    'ClaimForm',
    'MvpGithubAuthScreen',
    'MvpClaimScreen',
    'MvpAddAgentModal',
    'MvpDashboardChrome',
    'MvpPortfolioView',
    'MvpOverviewView',
    'MvpTransactionsView',
    'MvpCoinbaseOnrampModal',
    'AddressPicker',
    'QRScanner',
    'AddAddressModal',
    'MvpFundingView',
    'MvpSettingsView',
  ];

  function missingDashboardGlobals() {
    return REQUIRED_DASHBOARD_GLOBALS.filter((name) => window[name] === undefined || window[name] === null);
  }

  function waitForDashboardDependencies(maxAttempts = 120) {
    return new Promise((resolve) => {
      let attempts = 0;
      const tick = () => {
        const missing = missingDashboardGlobals();
        if (missing.length === 0 || attempts >= maxAttempts) {
          resolve(missing);
          return;
        }
        attempts += 1;
        setTimeout(tick, 50);
      };
      tick();
    });
  }

  const atomicToUsdc = (value) => {
    const parsed = Number.parseInt(String(value || '0'), 10);
    if (!Number.isFinite(parsed)) return 0;
    return parsed / 1_000_000;
  };

  const parseAtomic = (value) => {
    const parsed = Number.parseInt(String(value || '0'), 10);
    return Number.isFinite(parsed) ? parsed : 0;
  };

  const decimalUsdcToAtomic = (value) => {
    const text = String(value ?? '').trim();
    if (!text) return null;
    const match = text.match(/^(\d+)(?:\.(\d{0,6})\d*)?$/);
    if (!match) return null;
    const whole = Number.parseInt(match[1], 10);
    const fractional = Number.parseInt((match[2] || '').padEnd(6, '0'), 10);
    if (!Number.isFinite(whole) || !Number.isFinite(fractional)) return null;
    return whole * 1_000_000 + fractional;
  };

  function dashboardAvailableAtomic(account) {
    const gatewayFallback = account.gatewayAvailableAtomic || account.gatewayTotalAtomic || account.availableAtomic;
    const hasWalletBalance = account.circleAvailableAtomic || account.circleUsdcBalance;
    const hasGatewayBalance = account.gatewayTotalAtomic || account.gatewayUsdcTotal || account.gatewayAvailableAtomic || account.gatewayUsdcAvailable;
    if (!hasWalletBalance && !hasGatewayBalance) return String(gatewayFallback || '0');
    const circleAtomic = account.circleAvailableAtomic
      ? parseAtomic(account.circleAvailableAtomic)
      : (decimalUsdcToAtomic(account.circleUsdcBalance) || 0);
    const gatewayTotalAtomic = account.gatewayTotalAtomic
      ? parseAtomic(account.gatewayTotalAtomic)
      : (decimalUsdcToAtomic(account.gatewayUsdcTotal) || parseAtomic(account.gatewayAvailableAtomic));
    const pendingDepositsAtomic = account.gatewayPendingDepositsAtomic
      ? parseAtomic(account.gatewayPendingDepositsAtomic)
      : (decimalUsdcToAtomic(account.gatewayUsdcPendingDeposits) || 0);
    return String(Math.max(circleAtomic + gatewayTotalAtomic - pendingDepositsAtomic, 0));
  }

  const shortAddress = (value) => {
    const text = String(value || '').trim();
    if (!text) return 'none';
    if (text.length <= 18) return text;
    return `${text.slice(0, 10)}...${text.slice(-6)}`;
  };

  const entryAmountAtomic = (entry) => {
    const metadataAmount = entry?.metadata?.amountAtomic;
    if (metadataAmount) return String(metadataAmount);
    const available = Number.parseInt(String(entry?.availableDeltaAtomic || '0'), 10);
    const locked = Number.parseInt(String(entry?.lockedDeltaAtomic || '0'), 10);
    return String(Math.abs(available || locked || 0));
  };

  const entryStatus = (entry, account) => {
    let status = entry?.metadata?.dashboardStatus;
    if (
      status === 'pending_settle'
      && entry?.metadata?.transactionState === 'SETTLED'
      && entry?.metadata?.gatewayStage === 'pending_batch'
      && Number.parseInt(String(account.gatewayPendingBatchAtomic || '0'), 10) <= 0
    ) {
      return 'released';
    }
    if (status) return status;
    if (entry.entryType === 'credit') return 'onramp';
    if (entry.entryType === 'escrow_lock') return 'locked';
    if (entry.entryType === 'escrow_release' || entry.entryType === 'agent_transfer') return 'released';
    if (entry.entryType === 'escrow_refund') return 'refunded';
    return entry.entryType || 'processed';
  };

  function buildDashboardAgent(account, entries, escrows) {
    const agentId = String(account.agentId || '');
    const walletAddress = account.walletAddress || account.circleWalletId || agentId;
    const lifetimeInAtomic = entries.reduce((sum, entry) => {
      const delta = Number.parseInt(String(entry.availableDeltaAtomic || '0'), 10);
      return delta > 0 ? sum + delta : sum;
    }, 0);
    const lifetimeOutAtomic = entries.reduce((sum, entry) => {
      const delta = Number.parseInt(String(entry.availableDeltaAtomic || '0'), 10);
      return delta < 0 ? sum + Math.abs(delta) : sum;
    }, 0);
    const linkedPendingEntryIds = new Set(
      entries
        .filter((entry) => (
          entry.metadata?.dashboardStatus === 'credited'
          && entry.metadata?.linkedEntryId
        ))
        .map((entry) => String(entry.metadata.linkedEntryId)),
    );
    const visibleEntries = entries.filter(
      (entry) => !linkedPendingEntryIds.has(String(entry.entryId || '')),
    );
    const transactions = visibleEntries.map((entry) => {
      const amountAtomic = entryAmountAtomic(entry);
      const delta = Number.parseInt(String(entry.availableDeltaAtomic || '0'), 10);
      const status = entryStatus(entry, account);
      const escrow = escrows.find((item) => item.escrowId && item.escrowId === entry.escrowId);
      const counterparty = entry.metadata?.counterpartyEmail
        || entry.metadata?.counterparty
        || entry.reason
        || (escrow && (escrow.buyerAgentId === agentId ? escrow.sellerAgentId : escrow.buyerAgentId))
        || 'Ledger';
      return {
        id: entry.entryId,
        counterparty,
        amount: atomicToUsdc(amountAtomic),
        amountAtomic,
        direction: delta < 0 || entry.entryType === 'escrow_lock' || entry.entryType === 'withdrawal' ? 'out' : 'in',
        role: entry.entryType === 'withdrawal' || entry.entryType === 'withdrawal_submitted' ? 'withdrawal' : (delta < 0 ? 'payer' : 'payee'),
        status,
        timestamp: entry.createdAt,
        network: entry.metadata?.network,
        gatewayStage: entry.metadata?.gatewayStage,
        gasFeeAtomic: entry.metadata?.gasFeeAtomic,
        netAmountAtomic: entry.metadata?.netAmountAtomic,
        txHash: entry.metadata?.txHash,
      };
    });
    return {
      agent: {
        id: agentId,
        name: String(account.agentName || agentId),
        role: 'Agent Wallet Account',
        walletAddress: shortAddress(walletAddress),
        fullWalletAddress: String(walletAddress),
        claimedDaysAgo: 0,
        ownerEmail: account.email || account.dashboardClaimedByEmail || '',
      },
      balance: {
        available: atomicToUsdc(dashboardAvailableAtomic(account)),
        withdrawAvailable: atomicToUsdc(account.gatewayAvailableAtomic || account.gatewayWithdrawableAtomic || account.availableAtomic),
        withdrawAvailableAtomic: String(account.gatewayAvailableAtomic || account.gatewayWithdrawableAtomic || account.availableAtomic || '0'),
        locked: atomicToUsdc(account.lockedAtomic),
        lifetimeIn: atomicToUsdc(lifetimeInAtomic),
        lifetimeOut: atomicToUsdc(lifetimeOutAtomic),
        pendingSettlement: atomicToUsdc(0),
        pendingSettlementAtomic: '0',
      },
      transactions,
      settings: { limits: { perTradeCap: 0.01 } },
    };
  }

  function emptyDashboardState() {
    return { agents: {}, defaultAgentId: null, source: 'ledger-domain' };
  }

  function DashboardRouter() {
    const { authChecked, registered, claimed, currentUser } = window.useAppState();
    if (!authChecked) return <AuthCheckingScreen />;
    if (!registered || !currentUser) return <window.MvpGithubAuthScreen />;
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
    const shouldOpenDeepLinkClaim = !!(claimToken && deepLinkAgentId && !claimedAgents.includes(deepLinkAgentId));
    const [addAgentOpen, setAddAgentOpen] = React.useState(() => shouldOpenDeepLinkClaim);
    const [ledgerDashboardState, setLedgerDashboardState] = React.useState(null);

    React.useEffect(() => {
      let cancelled = false;
      if (!ownerEmail) {
        setLedgerDashboardState(emptyDashboardState());
        return () => { cancelled = true; };
      }
      fetch(`/ledger/accounts?claimedByEmail=${encodeURIComponent(ownerEmail)}`, { cache: 'no-store' })
        .then((response) => {
          if (!response.ok) throw new Error(`ledger accounts ${response.status}`);
          return response.json();
        })
        .then(async (accountsPayload) => {
          const accounts = Array.isArray(accountsPayload.accounts) ? accountsPayload.accounts : [];
          const agentPayloads = await Promise.all(accounts.map(async (account) => {
            const agentId = encodeURIComponent(account.agentId);
            const [entriesPayload, escrowsPayload] = await Promise.all([
              fetch(`/ledger/accounts/${agentId}/entries?limit=100`, { cache: 'no-store' }).then((response) => response.json()),
              fetch(`/ledger/accounts/${agentId}/escrows`, { cache: 'no-store' }).then((response) => response.json()),
            ]);
            return {
              account,
              entries: Array.isArray(entriesPayload.entries) ? entriesPayload.entries : [],
              escrows: Array.isArray(escrowsPayload.escrows) ? escrowsPayload.escrows : [],
            };
          }));
          if (cancelled) return;
          const agents = {};
          for (const item of agentPayloads) {
            agents[item.account.agentId] = buildDashboardAgent(item.account, item.entries, item.escrows);
          }
          const dashboardPayload = {
            agents,
            defaultAgentId: Object.keys(agents)[0] || null,
            source: 'ledger-domain',
          };
          if (!dashboardPayload.defaultAgentId) return;
          window.DASH_MOCK.day1 = dashboardPayload;
          window.DASH_CLAIMABLE = {};
          setLedgerDashboardState(dashboardPayload);
        })
        .catch(() => {
          // Keep the standalone day1 fixture when the backend is unavailable.
        });
      return () => { cancelled = true; };
    }, [ownerEmail, claimedAgents.join(',')]);

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

  async function mountDashboardRoot() {
    const missingDependencies = await waitForDashboardDependencies();
    if (missingDependencies.length > 0) {
      console.error('Dashboard dependencies failed to load', missingDependencies);
      const rootElement = document.getElementById('root');
      if (rootElement) {
        rootElement.textContent = 'Dashboard failed to load. Refresh the page.';
      }
      return;
    }
    const root = ReactDOM.createRoot(document.getElementById('root'));
    root.render(<DashboardRoot />);
  }

  mountDashboardRoot();
})();
