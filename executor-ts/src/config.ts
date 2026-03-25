import { parseEther } from "ethers";

export type AppConfig = {
  service: {
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
};

const DEFAULT_TESTNET_RPC_URL = "https://ethereum-sepolia-rpc.publicnode.com";
const DEFAULT_TESTNET_CHAIN_ID = 11155111;

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

export const HARDCODED_WHITELIST = [
  "0x000000000000000000000000000000000000dEaD",
  "0x1111111111111111111111111111111111111111",
] as const;

export const HARDCODED_SINGLE_TX_CAP_WEI = parseEther("1.0");

export function loadConfig(env: NodeJS.ProcessEnv = process.env): AppConfig {
  return {
    service: {
      port: parseNumberEnv(env, "EXECUTOR_PORT", 3000),
    },
    network: {
      rpcUrl: env.RPC_URL ?? DEFAULT_TESTNET_RPC_URL,
      expectedChainId: parseNumberEnv(env, "CHAIN_ID", DEFAULT_TESTNET_CHAIN_ID),
      mockChain: parseBooleanEnv(env, "EXECUTOR_MOCK_CHAIN", false),
      entryPointAddress:
        env.ENTRY_POINT_ADDRESS ?? "0x0576a174D229E3cFA37253523E645A78A0C91B57",
    },
    signer: {
      privateKey: env.PRIVATE_KEY,
    },
    policy: {
      dailyLimitWei: parseEthEnv(env, "DAILY_LIMIT", "2.0"),
      singleTxCapWei: parseEthEnv(env, "SINGLE_TX_CAP", "1.0"),
      whitelist: [...HARDCODED_WHITELIST, ...parseCsvEnv(env, "WHITELISTED_RECIPIENTS")],
    },
    execution: {
      bundlerRpcUrl: env.BUNDLER_RPC_URL,
    },
  };
}
