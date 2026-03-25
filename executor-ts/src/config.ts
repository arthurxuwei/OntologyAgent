import { parseEther } from "ethers";

function parseEthEnv(envName: string, defaultValue: string): bigint {
  const rawValue = process.env[envName] ?? defaultValue;
  try {
    return parseEther(rawValue);
  } catch {
    throw new Error(`${envName} must be a valid ETH amount string`);
  }
}

function parseNumberEnv(envName: string, defaultValue: number): number {
  const rawValue = process.env[envName];
  if (rawValue === undefined) {
    return defaultValue;
  }

  const parsed = Number(rawValue);
  if (!Number.isFinite(parsed) || parsed <= 0) {
    throw new Error(`${envName} must be a positive number`);
  }
  return parsed;
}

function parseCsvEnv(envName: string): string[] {
  const rawValue = process.env[envName];
  if (!rawValue) {
    return [];
  }
  return rawValue
    .split(",")
    .map((entry) => entry.trim())
    .filter((entry) => entry.length > 0);
}

function parseBooleanEnv(envName: string, defaultValue: boolean): boolean {
  const rawValue = process.env[envName];
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

export const config = {
  servicePort: parseNumberEnv("EXECUTOR_PORT", 3000),
  rpcUrl: process.env.RPC_URL ?? "https://ethereum-rpc.publicnode.com",
  privateKey: process.env.PRIVATE_KEY,
  dailyLimitWei: parseEthEnv("DAILY_LIMIT", "2.0"),
  singleTxCapWei: parseEthEnv("SINGLE_TX_CAP", "1.0"),
  envWhitelist: parseCsvEnv("WHITELISTED_RECIPIENTS"),
  x402DefaultRetries: parseNumberEnv("X402_MAX_RETRIES", 1),
  bundlerRpcUrl: process.env.BUNDLER_RPC_URL,
  mockChain: parseBooleanEnv("EXECUTOR_MOCK_CHAIN", false),
  entryPointAddress:
    process.env.ENTRY_POINT_ADDRESS ??
    "0x0576a174D229E3cFA37253523E645A78A0C91B57",
};
