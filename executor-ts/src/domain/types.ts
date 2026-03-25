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

export type X402FetchCommand = UpstreamRequest;

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
