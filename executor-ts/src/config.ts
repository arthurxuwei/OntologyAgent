import { parseEther, parseUnits } from "ethers";

export type AppConfig = {
  mcp: {
    port: number;
  };
  network: {
    rpcUrl: string;
    expectedChainId: number;
    mockChain: boolean;
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
};

const DEFAULT_TESTNET_RPC_URL = "https://base-sepolia-rpc.publicnode.com";
const DEFAULT_TESTNET_CHAIN_ID = 84532;
const DEFAULT_X402_NETWORK = "eip155:84532";
const DEFAULT_BASE_SEPOLIA_USDC = "0x036CbD53842c5426634e7929541eC2318f3dCF7e";
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
  if (!Number.isFinite(parsed) || parsed <= 0) {
    throw new Error(`${envName} must be a positive number`);
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
  return {
    mcp: {
      port: parseNumberEnv(env, "EXECUTOR_MCP_PORT", 8091),
    },
    network: {
      rpcUrl: env.RPC_URL ?? DEFAULT_TESTNET_RPC_URL,
      expectedChainId: parseNumberEnv(env, "CHAIN_ID", DEFAULT_TESTNET_CHAIN_ID),
      mockChain: parseBooleanEnv(env, "EXECUTOR_MOCK_CHAIN", false),
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
  };
}
