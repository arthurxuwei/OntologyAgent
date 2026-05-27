// MVP dashboard mock data. Same shape as main project's mockData
// (DASH_MOCK[state].agents, DASH_CLAIMABLE) so the reused AgentSwitcher /
// MockStateToggle / AddAgentModal keep working — but the content is
// stripped to fit MVP scope:
//
//   - No `selfCredit` block on each agent (MVP has no credit story).
//   - No `counterparties` block (no credit lookup view).
//   - `settings.limits.perTradeCap` seeded at 0.01 USDC. SettingsView lets
//     the user edit it; the edited value is persisted per-agent in
//     localStorage (kovaloop_mvp_dash_caps) and overrides this seed at read
//     time. Funding's withdraw min (5 USDC) is independent.
//   - No `approvedCounterparties` / `pendingApprovals` (MVP has no
//     approval flow — the agent operates within its per-trade cap).
//   - Transactions cover only the MVP loop: onramps, A2A pays, withdrawals.
//
// Three lifecycle states (matches main's mockState toggle):
//   empty  : just-registered, nothing happened yet.
//   day1   : a few transactions on agentA — the post-onboarding "first day"
//            scenario the demo's S3 ledger view shows.
//   mature : agentA + agentB, two weeks in, regular A2A flow + withdraw.
//            Demonstrates multi-agent + the Funding closed loop in steady-state.

(function () {
  const AGENT_A_META = {
    id: 'agentA',
    name: 'agentA',
    role: 'Document Workflow Assistant',
    walletAddress: '0x7a2c1e8f3b…4f91',
  };

  const AGENT_B_META = {
    id: 'agentB',
    name: 'agentB',
    role: 'Data Provider',
    walletAddress: '0x9b4d2a7c5e…8d23',
  };

  // ---- agentA payloads ---------------------------------------------------

  const AGENT_A_EMPTY = {
    agent: { ...AGENT_A_META, claimedDaysAgo: 0 },
    balance: { available: 0, lifetimeIn: 0, lifetimeOut: 0 },
    transactions: [],
    settings: {
      limits: { perTradeCap: 0.01 },
    },
  };

  const AGENT_A_DAY1 = {
    agent: { ...AGENT_A_META, claimedDaysAgo: 1 },
    balance: { available: 10.00, lifetimeIn: 10.00, lifetimeOut: 0 },
    transactions: [
      { id: 'onramp_8k1', counterparty: 'Coinbase Onramp', amount: 10.00, direction: 'in',  role: 'deposit', status: 'onramp', timestamp: '2 hr ago' },
    ],
    settings: {
      limits: { perTradeCap: 0.01 },
    },
  };

  const AGENT_A_MATURE = {
    agent: { ...AGENT_A_META, claimedDaysAgo: 14 },
    balance: { available: 38.512, lifetimeIn: 110.00, lifetimeOut: 71.488 },
    transactions: [
      { id: 'r_M9P4', counterparty: 'agentB',                amount: 0.001, direction: 'out', role: 'payer',      status: 'released', timestamp: '3 min ago' },
      { id: 'r_K7L2', counterparty: 'External · 0x742d3…A0c3', amount: 6.00,  direction: 'out', role: 'withdrawal', status: 'released', timestamp: '2 hr ago' },
      { id: 'r_F4A2', counterparty: 'agentB',                amount: 0.001, direction: 'out', role: 'payer',      status: 'released', timestamp: 'yesterday' },
      { id: 'r_D8E1', counterparty: 'agentB',                amount: 0.001, direction: 'out', role: 'payer',      status: 'released', timestamp: 'yesterday' },
      { id: 'r_B2C9', counterparty: 'Coinbase Onramp',       amount: 50.00, direction: 'in',  role: 'deposit',    status: 'onramp',   timestamp: '3 days ago' },
      { id: 'r_A1F3', counterparty: 'agentB',                amount: 0.001, direction: 'out', role: 'payer',      status: 'released', timestamp: '4 days ago' },
      { id: 'r_2N5G', counterparty: 'External · 0x742d3…A0c3', amount: 25.00, direction: 'out', role: 'withdrawal', status: 'released', timestamp: '5 days ago' },
      { id: 'r_3X4M', counterparty: 'agentB',                amount: 0.001, direction: 'out', role: 'payer',      status: 'released', timestamp: '6 days ago' },
      { id: 'onramp_4a',   counterparty: 'Coinbase Onramp',       amount: 50.00, direction: 'in',  role: 'deposit',    status: 'onramp',   timestamp: '8 days ago' },
      { id: 'onramp_init', counterparty: 'Coinbase Onramp',       amount: 10.00, direction: 'in',  role: 'deposit',    status: 'onramp',   timestamp: '14 days ago' },
    ],
    settings: {
      limits: { perTradeCap: 0.01 },
    },
  };

  // ---- agentB payload (mature only) --------------------------------------
  // agentB is the data provider — receives many small payments from agentA.

  const AGENT_B_MATURE = {
    agent: { ...AGENT_B_META, claimedDaysAgo: 9 },
    balance: { available: 0.247, lifetimeIn: 0.247, lifetimeOut: 0 },
    transactions: [
      { id: 'r_M9P4', counterparty: 'agentA', amount: 0.001, direction: 'in', role: 'payee', status: 'released', timestamp: '3 min ago' },
      { id: 'r_F4A2', counterparty: 'agentA', amount: 0.001, direction: 'in', role: 'payee', status: 'released', timestamp: 'yesterday' },
      { id: 'r_D8E1', counterparty: 'agentA', amount: 0.001, direction: 'in', role: 'payee', status: 'released', timestamp: 'yesterday' },
      { id: 'r_A1F3', counterparty: 'agentA', amount: 0.001, direction: 'in', role: 'payee', status: 'released', timestamp: '4 days ago' },
      { id: 'r_3X4M', counterparty: 'agentA', amount: 0.001, direction: 'in', role: 'payee', status: 'released', timestamp: '6 days ago' },
    ],
    settings: {
      limits: { perTradeCap: 0.01 },
    },
  };

  // ---- Claimable pool (used by AddAgentModal) ----------------------------
  // One simple option keeps the multi-agent flow demonstrable without
  // pulling in more characters than MVP needs.

  function freshAgent(meta) {
    return {
      agent: { ...meta, claimedDaysAgo: 0 },
      balance: { available: 0, lifetimeIn: 0, lifetimeOut: 0 },
      transactions: [],
      settings: { limits: { perTradeCap: 0.01 } },
    };
  }

  const DASH_CLAIMABLE = {
    agentX: freshAgent({
      id: 'agentX',
      name: 'agentX',
      role: 'Image Generator',
      walletAddress: '0xa3b1c4d2e7…f897',
    }),
  };

  const DASH_MOCK = {
    empty: {
      agents: { agentA: AGENT_A_EMPTY },
      defaultAgentId: 'agentA',
    },
    day1: {
      agents: { agentA: AGENT_A_DAY1 },
      defaultAgentId: 'agentA',
    },
    mature: {
      agents: { agentA: AGENT_A_MATURE, agentB: AGENT_B_MATURE },
      defaultAgentId: 'agentA',
    },
  };

  window.DASH_MOCK = DASH_MOCK;
  window.DASH_CLAIMABLE = DASH_CLAIMABLE;
})();
