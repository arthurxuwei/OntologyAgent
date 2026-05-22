// MVP AppStateProvider — same contract as src/dashboard/Shell/AppStateProvider
// but with `chief_mvp_dash_*` localStorage keys so the MVP dashboard's state
// is fully isolated from the main project's dashboard.
//
// Surfaces the same `useAppState()` hook (registers as window.useAppState).
// Reused as-is by the onboarding screens, AgentSwitcher, MockStateToggle,
// AddAgentModal — those all read via window.useAppState() and don't care
// which provider is mounted.

(function () {
  const AppStateContext = React.createContext(null);

  const STORAGE_KEYS = {
    registered:     'chief_mvp_dash_registered',
    mockState:      'chief_mvp_dash_mock_state',
    email:          'chief_mvp_dash_email',           // legacy email-only login, migrated into `user`
    user:           'chief_mvp_dash_user',             // {provider, login?, name?, email?, avatar_url?}
    wallet:         'chief_mvp_dash_wallet',           // legacy single-wallet, migrated on first read
    wallets:        'chief_mvp_dash_wallets',          // [{id, label, address, chain, createdAt}]
    defaultWallet:  'chief_mvp_dash_default_wallet',   // id string pointing into wallets
    agents:         'chief_mvp_dash_agents',
    activeAgent:    'chief_mvp_dash_active_agent',
  };

  const readBool = (key) => window.localStorage.getItem(key) === 'true';
  const writeBool = (key, value) => {
    if (value) window.localStorage.setItem(key, 'true');
    else window.localStorage.removeItem(key);
  };

  function generateWalletId() {
    return 'wal_' + Math.random().toString(36).slice(2, 8);
  }

  function readInitialAgents() {
    const raw = window.localStorage.getItem(STORAGE_KEYS.agents);
    if (raw) {
      try {
        const parsed = JSON.parse(raw);
        if (Array.isArray(parsed)) return parsed;
      } catch { /* fall through */ }
    }
    return [];
  }

  // Returns the canonical owner identity. Reads the new `user` blob first,
  // then forward-migrates the legacy `email` key into a `{provider: 'email'}`
  // shape so users who registered before the GitHub-OAuth ship keep working.
  // Also actively cleans up malformed values so they don't survive to fool
  // downstream "user exists?" checks.
  function readInitialUser() {
    const raw = window.localStorage.getItem(STORAGE_KEYS.user);
    if (raw) {
      try {
        const parsed = JSON.parse(raw);
        if (parsed && typeof parsed === 'object' && parsed.provider) return parsed;
      } catch { /* fall through */ }
      // Stored value exists but doesn't parse to a valid user — clear it
      // so the legacy-email check below (or downstream registered-self-heal)
      // sees the same world an empty key would.
      window.localStorage.removeItem(STORAGE_KEYS.user);
    }
    const legacyEmail = window.localStorage.getItem(STORAGE_KEYS.email);
    if (legacyEmail) {
      const migrated = { provider: 'email', email: legacyEmail };
      window.localStorage.setItem(STORAGE_KEYS.user, JSON.stringify(migrated));
      window.localStorage.removeItem(STORAGE_KEYS.email);
      return migrated;
    }
    return null;
  }

  function readInitialActiveAgent(agents) {
    const stored = window.localStorage.getItem(STORAGE_KEYS.activeAgent);
    if (stored) return stored;
    return agents[0] || null;
  }

  // Normalize an entry — fills in defaults for fields added after the schema
  // shipped (e.g. `chain`). Older saved wallets are upgraded transparently.
  function normalizeWallet(w) {
    return { ...w, chain: w.chain || 'base' };
  }

  // Migrates the legacy single-wallet `chief_mvp_dash_wallet` key into the
  // new `chief_mvp_dash_wallets` array on first read, and seeds defaultWallet.
  // Also forward-migrates any older array entries missing newly-added fields.
  function readInitialWallets() {
    const raw = window.localStorage.getItem(STORAGE_KEYS.wallets);
    if (raw) {
      try {
        const parsed = JSON.parse(raw);
        if (Array.isArray(parsed)) {
          const normalized = parsed.map(normalizeWallet);
          const drifted = normalized.some((w, i) => w.chain !== parsed[i].chain);
          if (drifted) {
            window.localStorage.setItem(STORAGE_KEYS.wallets, JSON.stringify(normalized));
          }
          return normalized;
        }
      } catch { /* fall through */ }
    }
    const legacy = window.localStorage.getItem(STORAGE_KEYS.wallet);
    if (legacy) {
      const migrated = [normalizeWallet({
        id: generateWalletId(),
        label: 'My Wallet',
        address: legacy,
        createdAt: Date.now(),
      })];
      window.localStorage.setItem(STORAGE_KEYS.wallets, JSON.stringify(migrated));
      window.localStorage.setItem(STORAGE_KEYS.defaultWallet, migrated[0].id);
      window.localStorage.removeItem(STORAGE_KEYS.wallet);
      return migrated;
    }
    return [];
  }

  function MvpAppStateProvider({ children }) {
    const params = React.useMemo(() => new URLSearchParams(window.location.search), []);
    const claimToken = params.get('claimCode') || '';
    const deepLinkAgentId = params.get('agentId') || '';
    const internalMode = params.get('internal') === '1';
    const isReset = params.get('reset') === '1';

    // Dev seed (?seed=<mockState>): pre-populates localStorage to a post-
    // onboarding state so screenshot scripts and dev iteration can land
    // directly on the dashboard surface. Strips the param after seeding so
    // a refresh doesn't re-seed and clobber any new state.
    React.useEffect(() => {
      const seed = params.get('seed');
      if (!seed) return;
      if (!readBool(STORAGE_KEYS.registered)) {
        window.localStorage.setItem(STORAGE_KEYS.registered, 'true');
        window.localStorage.setItem(STORAGE_KEYS.user, JSON.stringify({
          provider: 'github',
          login: 'william',
          name: 'William',
          email: 'william@example.com',
          avatar_url: null,
        }));
        window.localStorage.setItem(STORAGE_KEYS.agents, JSON.stringify(['agentA']));
        window.localStorage.setItem(STORAGE_KEYS.activeAgent, 'agentA');
      }
      if (['empty', 'day1', 'mature'].includes(seed)) {
        window.localStorage.setItem(STORAGE_KEYS.mockState, seed);
      }
      const url = new URL(window.location.href);
      url.searchParams.delete('seed');
      window.history.replaceState({}, '', url.toString());
      window.location.reload();
    }, [params]);

    React.useEffect(() => {
      if (!isReset) return;
      Object.values(STORAGE_KEYS).forEach((k) => window.localStorage.removeItem(k));
      const url = new URL(window.location.href);
      url.searchParams.delete('reset');
      window.history.replaceState({}, '', url.toString());
      window.location.reload();
    }, [isReset]);

    // Order matters: currentUser runs first so its migration (legacy email →
    // user) and malformed-value cleanup execute before `registered` evaluates
    // — registered then self-heals against the post-migration storage state.
    const [currentUser, setCurrentUserState] = React.useState(() => readInitialUser());
    const [registered, setRegisteredState] = React.useState(() => {
      // Self-heal: a `registered=true` flag with no user identity is a
      // pre-GitHub-ship zombie state (e.g., legacy session that lost its
      // email key along the way). Force re-auth rather than render the
      // dashboard with an empty handle. Also strip the stale flag so it
      // doesn't keep replaying every load.
      const hasUser = !!window.localStorage.getItem(STORAGE_KEYS.user);
      if (!hasUser) {
        window.localStorage.removeItem(STORAGE_KEYS.registered);
        return false;
      }
      return readBool(STORAGE_KEYS.registered);
    });
    const [mockState, setMockStateState]   = React.useState(
      () => window.localStorage.getItem(STORAGE_KEYS.mockState) || 'day1'
    );
    const [wallets, setWalletsState] = React.useState(() => readInitialWallets());
    const [defaultWalletId, setDefaultWalletIdState] = React.useState(
      () => window.localStorage.getItem(STORAGE_KEYS.defaultWallet) || null
    );
    const [agents, setAgentsState] = React.useState(() => readInitialAgents());
    const [activeAgentId, setActiveAgentIdState] = React.useState(
      () => readInitialActiveAgent(readInitialAgents())
    );
    const [authChecked, setAuthChecked] = React.useState(() => !!params.get('seed'));

    const writeActiveAgent = React.useCallback((id) => {
      if (id) window.localStorage.setItem(STORAGE_KEYS.activeAgent, id);
      else window.localStorage.removeItem(STORAGE_KEYS.activeAgent);
      setActiveAgentIdState(id);
    }, []);

    const addWallet = React.useCallback((label, address, opts = {}) => {
      if (!address) return null;
      const id = generateWalletId();
      let becameDefault = false;
      setWalletsState((prev) => {
        const newWallet = {
          id,
          label: label || `Wallet ${prev.length + 1}`,
          address,
          chain: opts.chain || 'base',
          createdAt: Date.now(),
        };
        const next = [...prev, newWallet];
        window.localStorage.setItem(STORAGE_KEYS.wallets, JSON.stringify(next));
        if (prev.length === 0 || opts.setAsDefault) {
          window.localStorage.setItem(STORAGE_KEYS.defaultWallet, id);
          becameDefault = true;
        }
        return next;
      });
      if (becameDefault) setDefaultWalletIdState(id);
      return id;
    }, []);

    const removeWallet = React.useCallback((id) => {
      if (!id) return;
      const currentDefault = window.localStorage.getItem(STORAGE_KEYS.defaultWallet);
      let reassignedDefault = null;
      let didReassign = false;
      setWalletsState((prev) => {
        const next = prev.filter(w => w.id !== id);
        window.localStorage.setItem(STORAGE_KEYS.wallets, JSON.stringify(next));
        if (currentDefault === id) {
          reassignedDefault = next[0]?.id || null;
          didReassign = true;
          if (reassignedDefault) {
            window.localStorage.setItem(STORAGE_KEYS.defaultWallet, reassignedDefault);
          } else {
            window.localStorage.removeItem(STORAGE_KEYS.defaultWallet);
          }
        }
        return next;
      });
      if (didReassign) setDefaultWalletIdState(reassignedDefault);
    }, []);

    const setDefaultWallet = React.useCallback((id) => {
      if (!id) {
        window.localStorage.removeItem(STORAGE_KEYS.defaultWallet);
        setDefaultWalletIdState(null);
        return;
      }
      window.localStorage.setItem(STORAGE_KEYS.defaultWallet, id);
      setDefaultWalletIdState(id);
    }, []);

    const updateWalletLabel = React.useCallback((id, label) => {
      if (!id) return;
      setWalletsState((prev) => {
        const next = prev.map(w => w.id === id ? { ...w, label } : w);
        window.localStorage.setItem(STORAGE_KEYS.wallets, JSON.stringify(next));
        return next;
      });
    }, []);

    // signIn — canonical sign-in path. Accepts a fully-formed user object
    // (provider + identity fields). Persists to localStorage and flips
    // `registered` true. Replaces the older `setRegistered(true, {email})`
    // pattern; that shim still exists below for any leftover callers.
    const signIn = React.useCallback((user) => {
      if (!user || !user.provider) return;
      const previousEmail = readInitialUser()?.email || '';
      const nextEmail = user.email || '';
      if (previousEmail && nextEmail && previousEmail !== nextEmail) {
        window.localStorage.removeItem(STORAGE_KEYS.agents);
        window.localStorage.removeItem(STORAGE_KEYS.activeAgent);
        setAgentsState([]);
        setActiveAgentIdState(null);
      }
      window.localStorage.setItem(STORAGE_KEYS.user, JSON.stringify(user));
      setCurrentUserState(user);
      writeBool(STORAGE_KEYS.registered, true);
      setRegisteredState(true);
    }, []);

    const signOut = React.useCallback(() => {
      window.localStorage.removeItem(STORAGE_KEYS.user);
      window.localStorage.removeItem(STORAGE_KEYS.agents);
      window.localStorage.removeItem(STORAGE_KEYS.activeAgent);
      setCurrentUserState(null);
      setAgentsState([]);
      setActiveAgentIdState(null);
      writeBool(STORAGE_KEYS.registered, false);
      setRegisteredState(false);
    }, []);

    React.useEffect(() => {
      if (params.get('seed')) {
        setAuthChecked(true);
        return;
      }
      let cancelled = false;
      fetch('/auth/session', { credentials: 'same-origin' })
        .then((response) => {
          if (!response.ok) throw new Error(`auth session ${response.status}`);
          return response.json();
        })
        .then((payload) => {
          if (cancelled) return;
          if (payload && payload.authenticated && payload.user) {
            signIn(payload.user);
          } else {
            if (claimToken && deepLinkAgentId) {
              window.location.href = `/auth/github/login?returnTo=${encodeURIComponent(window.location.pathname + window.location.search)}`;
              return;
            }
            signOut();
          }
        })
        .catch(() => {
          if (!cancelled) signOut();
        })
        .finally(() => {
          if (!cancelled) setAuthChecked(true);
        });
      return () => { cancelled = true; };
    }, [params, signIn, signOut, claimToken, deepLinkAgentId]);

    // Legacy shim — main project's RegistrationScreen (no longer mounted by
    // MVP but still in the bundle) calls setRegistered(true, {email}). Routes
    // through signIn so the storage shape stays consistent.
    const setRegistered = React.useCallback((value, opts = {}) => {
      if (value && opts.email !== undefined) {
        signIn({ provider: 'email', email: opts.email });
      } else if (!value) {
        signOut();
      } else {
        writeBool(STORAGE_KEYS.registered, value);
        setRegisteredState(value);
      }
      if (opts.externalWallet) {
        addWallet('My Wallet', opts.externalWallet);
      }
    }, [signIn, signOut, addWallet]);

    const claimAgent = React.useCallback((agentId) => {
      if (!agentId) return;
      setAgentsState((prev) => {
        const next = prev.includes(agentId) ? prev : [...prev, agentId];
        window.localStorage.setItem(STORAGE_KEYS.agents, JSON.stringify(next));
        return next;
      });
      writeActiveAgent(agentId);
    }, [writeActiveAgent]);

    // Backwards-compat shim for older callers that only flip claimed=true.
    const setClaimed = React.useCallback((value) => {
      if (value && agents[0]) claimAgent(agents[0]);
    }, [agents, claimAgent]);

    const setActiveAgent = React.useCallback((id) => {
      if (id) writeActiveAgent(id);
    }, [writeActiveAgent]);

    const setMockState = React.useCallback((value) => {
      window.localStorage.setItem(STORAGE_KEYS.mockState, value);
      setMockStateState(value);
    }, []);

    // Backwards-compat shim: onboarding + existing SettingsView still call this.
    // Treats it as "set the default wallet's address" — update in place when a
    // default exists, otherwise create a new wallet and mark it default.
    const setExternalWallet = React.useCallback((value) => {
      const currentDefault = window.localStorage.getItem(STORAGE_KEYS.defaultWallet);
      if (!value) {
        if (!currentDefault) return;
        let newDefault = null;
        setWalletsState((prev) => {
          const next = prev.filter(w => w.id !== currentDefault);
          window.localStorage.setItem(STORAGE_KEYS.wallets, JSON.stringify(next));
          newDefault = next[0]?.id || null;
          if (newDefault) {
            window.localStorage.setItem(STORAGE_KEYS.defaultWallet, newDefault);
          } else {
            window.localStorage.removeItem(STORAGE_KEYS.defaultWallet);
          }
          return next;
        });
        setDefaultWalletIdState(newDefault);
        return;
      }
      let newId = null;
      setWalletsState((prev) => {
        if (currentDefault && prev.some(w => w.id === currentDefault)) {
          const next = prev.map(w => w.id === currentDefault ? { ...w, address: value } : w);
          window.localStorage.setItem(STORAGE_KEYS.wallets, JSON.stringify(next));
          return next;
        }
        newId = generateWalletId();
        const newWallet = {
          id: newId,
          label: 'My Wallet',
          address: value,
          chain: 'base',
          createdAt: Date.now(),
        };
        const next = [...prev, newWallet];
        window.localStorage.setItem(STORAGE_KEYS.wallets, JSON.stringify(next));
        window.localStorage.setItem(STORAGE_KEYS.defaultWallet, newId);
        return next;
      });
      if (newId) setDefaultWalletIdState(newId);
    }, []);

    const resetAll = React.useCallback(() => {
      Object.values(STORAGE_KEYS).forEach((k) => window.localStorage.removeItem(k));
      setRegisteredState(false);
      setMockStateState('day1');
      setCurrentUserState(null);
      setWalletsState([]);
      setDefaultWalletIdState(null);
      setAgentsState([]);
      setActiveAgentIdState(null);
    }, []);

    if (isReset) {
      return <div style={{ background: 'var(--surface-paper)', minHeight: '100vh' }} />;
    }

    const claimed = agents.length > 0;

    // Derived: the address of the wallet currently flagged as default. Keeps
    // FundingView / SettingsView / onboarding working without change while
    // the new wallets[] becomes the canonical store.
    const externalWallet = React.useMemo(() => {
      const w = wallets.find((w) => w.id === defaultWalletId);
      return w?.address || '';
    }, [wallets, defaultWalletId]);

    // Derived: backwards-compat alias so older consumers (Chrome / Overview /
    // Settings before this change) reading `ownerEmail` continue to work
    // regardless of which provider signed the user in.
    const ownerEmail = currentUser?.email || '';

    const value = {
      authChecked, registered, currentUser, ownerEmail, externalWallet, mockState,
      wallets, defaultWalletId,
      agents, activeAgentId, claimed,
      claimToken, deepLinkAgentId, internalMode,
      signIn, signOut,
      setRegistered, setClaimed, claimAgent, setActiveAgent,
      setMockState, setExternalWallet,
      addWallet, removeWallet, setDefaultWallet, updateWalletLabel,
      resetAll,
    };

    return (
      <AppStateContext.Provider value={value}>
        {children}
      </AppStateContext.Provider>
    );
  }

  function useAppState() {
    const ctx = React.useContext(AppStateContext);
    if (!ctx) throw new Error('useAppState must be used inside MvpAppStateProvider');
    return ctx;
  }

  window.AppStateContext = AppStateContext;
  window.AppStateProvider = MvpAppStateProvider;
  window.useAppState = useAppState;
})();
