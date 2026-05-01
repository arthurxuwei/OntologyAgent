import { parseEther, parseUnits } from "ethers";

export type AppConfig = {
  mcp: {
    port: number;
  };
  network: {
    rpcUrl: string;
    expectedChainId: number;
    mockChain: boolean;
    mockBalanceWei: bigint;
    mockUsdcBalanceAtomic: bigint;
    entryPointAddress: string;
  };
  signer: {
    privateKey?: string;
  };
  policy: {
    dailyLimitWei: bigint;
    singleTxCapWei: bigint;
    whitelist: string[];
  };
  execution: {
    bundlerRpcUrl?: string;
    tradeIntentPair: string;
    tradeIntentSellToken: string;
    tradeIntentBuyToken: string;
  };
  x402: {
    facilitatorUrl: string;
    network: string;
    buyerPrivateKey?: string;
    usdcAssetAddress: string;
    usdcDecimals: number;
    usdcSingleCapAtomic: bigint;
    usdcDailyCapAtomic: bigint;
  };
  circle: {
    apiKey?: string;
    entitySecret?: string;
    // Static ciphertext is loaded only so live wallet creation can reject unsafe reuse.
    // Circle requires a fresh entitySecretCiphertext for each API request.
    entitySecretCiphertext?: string;
    walletSetId?: string;
    baseUrl: string;
    blockchain: "BASE-SEPOLIA";
  };
  agentWallet: {
    statePath?: string;
  };
};

const DEFAULT_TESTNET_RPC_URL = "https://base-sepolia-rpc.publicnode.com";
const DEFAULT_TESTNET_CHAIN_ID = 84532;
const DEFAULT_X402_NETWORK = "eip155:84532";
const DEFAULT_BASE_SEPOLIA_USDC = "0x036CbD53842c5426634e7929541eC2318f3dCF7e";
const DEFAULT_BASE_SEPOLIA_WETH = "0x4200000000000000000000000000000000000006";
const DEFAULT_CIRCLE_BASE_URL = "https://api.circle.com/v1/w3s";
const X402_USDC_DECIMALS = 6;

function parseEthEnv(
  env: NodeJS.ProcessEnv,
  envName: string,
  defaultValue: string,
): bigint {
  const rawValue = env[envName] ?? defaultValue;
  try {
    return parseEther(rawValue);
  } catch {
    throw new Error(`${envName} must be a valid ETH amount string`);
  }
}

function parseUnitsEnv(
  env: NodeJS.ProcessEnv,
  envName: string,
  defaultValue: string,
  decimals: number,
): bigint {
  const rawValue = env[envName] ?? defaultValue;
  try {
    return parseUnits(rawValue, decimals);
  } catch {
    throw new Error(`${envName} must be a valid decimal amount string`);
  }
}

function parseNumberEnv(
  env: NodeJS.ProcessEnv,
  envName: string,
  defaultValue: number,
): number {
  const rawValue = env[envName];
  if (rawValue === undefined) {
    return defaultValue;
  }

  const parsed = Number(rawValue);
  if (!Number.isFinite(parsed) || !Number.isInteger(parsed) || parsed <= 0) {
    throw new Error(`${envName} must be a positive integer`);
  }
  return parsed;
}

function parseCsvEnv(env: NodeJS.ProcessEnv, envName: string): string[] {
  const rawValue = env[envName];
  if (!rawValue) {
    return [];
  }
  return rawValue
    .split(",")
    .map((entry) => entry.trim())
    .filter((entry) => entry.length > 0);
}

function parseBooleanEnv(
  env: NodeJS.ProcessEnv,
  envName: string,
  defaultValue: boolean,
): boolean {
  const rawValue = env[envName];
  if (rawValue === undefined) {
    return defaultValue;
  }
  const normalized = rawValue.trim().toLowerCase();
  if (["1", "true", "yes", "on"].includes(normalized)) {
    return true;
  }
  if (["0", "false", "no", "off"].includes(normalized)) {
    return false;
  }
  throw new Error(`${envName} must be a boolean`);
}

function pickOptionalEnv(...values: Array<string | undefined>): string | undefined {
  for (const value of values) {
    if (value !== undefined && value.trim() !== "") {
      return value;
    }
  }
  return undefined;
}

function normalizePrivateKey(value?: string): string | undefined {
  if (value === undefined) {
    return undefined;
  }

  const trimmed = value.trim();
  if (trimmed === "") {
    return undefined;
  }

  return trimmed.startsWith("0x") ? trimmed : `0x${trimmed}`;
}

export const HARDCODED_WHITELIST = [
  "0x000000000000000000000000000000000000dEaD",
  "0x1111111111111111111111111111111111111111",
] as const;

export const HARDCODED_SINGLE_TX_CAP_WEI = parseEther("1.0");

export function loadConfig(env: NodeJS.ProcessEnv = process.env): AppConfig {
  const chainMcpPortEnv = env.CHAIN_MCP_PORT ?? env.EXECUTOR_MCP_PORT;
  const chainMockEnv = env.CHAIN_MOCK ?? env.EXECUTOR_MOCK_CHAIN;

  return {
    mcp: {
      port: parseNumberEnv({ ...env, CHAIN_MCP_PORT: chainMcpPortEnv }, "CHAIN_MCP_PORT", 8091),
    },
    network: {
      rpcUrl: env.RPC_URL ?? DEFAULT_TESTNET_RPC_URL,
      expectedChainId: parseNumberEnv(env, "CHAIN_ID", DEFAULT_TESTNET_CHAIN_ID),
      mockChain: parseBooleanEnv({ ...env, CHAIN_MOCK: chainMockEnv }, "CHAIN_MOCK", false),
      mockBalanceWei: parseEthEnv(env, "CHAIN_MOCK_BALANCE_ETH", "1.0"),
      mockUsdcBalanceAtomic: parseUnitsEnv(env, "CHAIN_MOCK_USDC_BALANCE", "0", X402_USDC_DECIMALS),
      entryPointAddress:
        env.ENTRY_POINT_ADDRESS ?? "0x0576a174D229E3cFA37253523E645A78A0C91B57",
    },
    signer: {
      privateKey: normalizePrivateKey(env.PRIVATE_KEY),
    },
    policy: {
      dailyLimitWei: parseEthEnv(env, "DAILY_LIMIT", "2.0"),
      singleTxCapWei: parseEthEnv(env, "SINGLE_TX_CAP", "1.0"),
      whitelist: [...HARDCODED_WHITELIST, ...parseCsvEnv(env, "WHITELISTED_RECIPIENTS")],
    },
    execution: {
      bundlerRpcUrl: env.BUNDLER_RPC_URL,
      tradeIntentPair: env.TRADE_INTENT_PAIR ?? "ETH/USDC",
      tradeIntentSellToken: env.TRADE_INTENT_SELL_TOKEN ?? DEFAULT_BASE_SEPOLIA_USDC,
      tradeIntentBuyToken: env.TRADE_INTENT_BUY_TOKEN ?? DEFAULT_BASE_SEPOLIA_WETH,
    },
    x402: {
      facilitatorUrl: env.X402_FACILITATOR_URL ?? "https://x402.org/facilitator",
      network: env.X402_NETWORK ?? DEFAULT_X402_NETWORK,
      buyerPrivateKey: normalizePrivateKey(
        pickOptionalEnv(env.X402_BUYER_PRIVATE_KEY, env.PRIVATE_KEY),
      ),
      usdcAssetAddress: env.X402_USDC_ASSET_ADDRESS ?? DEFAULT_BASE_SEPOLIA_USDC,
      usdcDecimals: X402_USDC_DECIMALS,
      usdcSingleCapAtomic: parseUnitsEnv(env, "X402_USDC_SINGLE_CAP", "1.0", X402_USDC_DECIMALS),
      usdcDailyCapAtomic: parseUnitsEnv(env, "X402_USDC_DAILY_CAP", "2.0", X402_USDC_DECIMALS),
    },
    circle: {
      apiKey: pickOptionalEnv(env.CIRCLE_API_KEY),
      entitySecret: pickOptionalEnv(env.CIRCLE_ENTITY_SECRET),
      entitySecretCiphertext: pickOptionalEnv(env.CIRCLE_ENTITY_SECRET_CIPHERTEXT),
      walletSetId: pickOptionalEnv(env.CIRCLE_WALLET_SET_ID),
      baseUrl: env.CIRCLE_BASE_URL ?? DEFAULT_CIRCLE_BASE_URL,
      blockchain: "BASE-SEPOLIA",
    },
    agentWallet: {
      statePath: pickOptionalEnv(env.AGENT_WALLET_STATE_PATH),
    },
  };
}
