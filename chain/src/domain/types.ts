export type PolicyAction =
  | "transfer-sign"
  | "execution-submit"
  | "user-operation-submit"
  | "x402-fetch";

export type PolicySnapshot = {
  dayKey: string;
  spentTodayWei: string;
  dailyLimitWei: string;
  spentTodayUsdcAtomic: string;
  dailyLimitUsdcAtomic: string;
};

export type PolicyDecision = {
  action: PolicyAction;
  normalizedTo: string;
  amountWei: string;
  allowed: true;
};

export type TransferSignCommand = {
  to: string;
  amountEth: string;
};

export type UpstreamRequest = {
  url: string;
  method?: string;
  headers?: Record<string, string>;
  body?: unknown;
};

export type ExecutionCommand = {
  to: string;
  valueEth?: string;
  data?: string;
};

export type UserOperationCommand = {
  target: string;
  maxCostEth: string;
  raw: Record<string, unknown>;
};

export type SignedTransfer = {
  from: string;
  to: string;
  amountWei: string;
  txHash: string;
  signedTx: string;
  mode: "mock" | "network";
};

export type SubmittedTransaction = {
  from: string;
  to: string;
  amountWei: string;
  txHash: string;
  mode: "mock" | "network";
};

export type SettlementResult = {
  kind: "signed" | "submitted" | "user-operation";
  identifier: string;
  mode: "mock" | "network";
};

export type SignTransferResult = {
  transfer: SignedTransfer;
  settlement: SettlementResult;
  decision: PolicyDecision;
  policy: PolicySnapshot;
};

export type ExecutionResult = {
  execution: SubmittedTransaction;
  settlement: SettlementResult;
  decision: PolicyDecision;
  policy: PolicySnapshot;
};

export type PaymentAttempt = {
  attempt: number;
  transaction: SubmittedTransaction;
  settlement: SettlementResult;
};

export type X402PaymentPreference = "standard" | "circle-gateway";

export type X402FetchCommand = UpstreamRequest & {
  paymentPreference?: X402PaymentPreference;
};

export type X402RequirementSummary = {
  scheme: string;
  network: string;
  asset: string;
  amount: string;
  payTo: string;
  maxTimeoutSeconds: number;
  extra: Record<string, unknown>;
};

export type X402SettleSummary = {
  success: boolean;
  transaction: string;
  network: string;
  payer?: string;
  errorReason?: string;
  errorMessage?: string;
  extensions?: Record<string, unknown>;
};

export type X402PolicyDecision = {
  action: "x402-fetch";
  normalizedTo: string;
  network: string;
  asset: string;
  amountAtomic: string;
  allowed: true;
};

export type X402FetchResult = {
  upstream: {
    status: number;
    contentType: string;
    payload: unknown;
  };
  payment: null | {
    requiredVersion: number;
    selected: X402RequirementSummary;
    response: X402SettleSummary;
  };
  decision: X402PolicyDecision | null;
  policy: PolicySnapshot;
};

export type AgentWalletInitCommand = {
  agentName: string;
  agentDescription?: string;
  agentId?: string;
  email?: string;
};

export type AgentWalletInitResult = {
  circleWalletId: string;
  circleWalletSetId: string | null;
  blockchain: "BASE-SEPOLIA";
  walletAddress: string;
  mode: "mock" | "circle";
  binding?: AgentWalletBinding;
};

export type AgentWalletStatusCommand = {
  walletAddress?: string;
  circleWalletId?: string;
};

export type AgentWalletGetOrCreateCommand = AgentWalletInitCommand &
  AgentWalletStatusCommand;

export type AgentWalletBinding = {
  agentName: string;
  agentId: string | null;
  email: string | null;
  walletAddress: string;
  circleWalletId: string | null;
  circleWalletSetId: string | null;
  blockchain: "BASE-SEPOLIA";
  mode: "mock" | "circle";
  updatedAt: string;
};

export type AgentWalletStatusResult = {
  circleWalletId: string | null;
  circleWalletSetId: string | null;
  blockchain: "BASE-SEPOLIA";
  walletAddress: string;
  status: "created" | "available" | "unknown";
  balances: Record<string, string>;
  mode: "mock" | "circle";
};

export type AgentWalletGetOrCreateResult =
  | (AgentWalletInitResult & {
      reused: false;
      binding?: AgentWalletBinding;
    })
  | (AgentWalletStatusResult & {
      reused: true;
      binding?: AgentWalletBinding;
    });

export type AgentWalletRegisterX402ServiceCommand = {
  name: string;
  path: string;
  priceAtomic: string;
  payTo: string;
};

export type AgentWalletRegisterX402ServiceResult = {
  name: string;
  path: string;
  priceAtomic: string;
  assetAddress: string;
  network: string;
  payTo: string;
  active: true;
};

export type AgentWalletTransferCommand = {
  fromAgentId?: string;
  fromAgentName?: string;
  fromCircleWalletId?: string;
  toAgentId?: string;
  toAgentName?: string;
  toAddress?: string;
  amountEth?: string;
  amountAtomic?: string;
  asset?: "ETH" | "USDC";
  refId?: string;
};

export type AgentWalletTransferResult = {
  fromAgentId: string | null;
  fromAgentName: string | null;
  fromCircleWalletId: string;
  fromAddress: string;
  toAgentId: string | null;
  toAgentName: string | null;
  toAddress: string;
  asset: "ETH" | "USDC";
  amount: string;
  amountEth: string | null;
  amountAtomic: string | null;
  tokenId: string | null;
  tokenAddress: string;
  blockchain: "BASE-SEPOLIA";
  transactionId: string | null;
  transactionHash: string | null;
  state: string | null;
  mode: "circle" | "gateway";
  raw: unknown;
};

export type AgentWalletTransactionStatusCommand = {
  transactionId: string;
};

export type AgentWalletTransactionStatusResult = {
  transactionId: string;
  transactionHash: string | null;
  state: string | null;
  raw: unknown;
};

export type AgentWalletFaucetCommand = {
  agentId?: string;
  agentName?: string;
  walletAddress?: string;
  native?: boolean;
  usdc?: boolean;
};

export type AgentWalletFaucetResult = {
  address: string;
  blockchain: "BASE-SEPOLIA";
  native: boolean;
  usdc: boolean;
  status: "requested";
};

export type AgentWalletCallX402ServiceCommand = X402FetchCommand;

export type AgentWalletCallX402ServiceResult = X402FetchResult & {
  agentWalletTool: "agent_wallet_call_x402_service";
};

export type UserOperationResult = {
  userOperation: {
    target: string;
    maxCostWei: string;
    userOpHash: string;
  };
  settlement: SettlementResult;
  decision: PolicyDecision;
  policy: PolicySnapshot;
};

export type TransactionReceiptStatusResult = {
  txHash: string;
  found: boolean;
  finalized: boolean;
  success: boolean;
  status: "pending" | "success" | "reverted";
  blockNumber: number | null;
  receipt: Record<string, unknown> | null;
  mode: "mock" | "network";
};

export type UserOperationStatusResult = {
  userOpHash: string;
  found: boolean;
  finalized: boolean;
  success: boolean;
  status: "pending" | "success" | "failed";
  txHash: string | null;
  receipt: Record<string, unknown> | null;
  mode: "mock" | "network";
};

export type HealthResult = {
  service: string;
  status: "ok";
  chain: {
    blockNumber: number;
    rpcUrl: string;
    chainId: number | null;
    expectedChainId: number;
    mockChain: boolean;
  };
  policy: PolicySnapshot;
  x402: {
    facilitatorUrl: string;
    network: string;
    asset: string;
    buyerSignerConfigured: boolean;
  };
};

export type WalletStateResult = {
  wallet: {
    address: string | null;
    signerConfigured: boolean;
    balanceWei: string;
    balanceEth: string;
    usdcBalanceAtomic: string;
    usdcBalance: string;
    mockChain: boolean;
  };
  chain: {
    blockNumber: number;
    rpcUrl: string;
    chainId: number | null;
    expectedChainId: number;
    mockChain: boolean;
  };
  policy: PolicySnapshot;
  x402: {
    network: string;
    asset: string;
    buyerSignerConfigured: boolean;
  };
};
