import { getAddress, isAddress } from "ethers";

import { config, HARDCODED_SINGLE_TX_CAP_WEI, HARDCODED_WHITELIST } from "./config.js";

const whitelist = new Set(
  [...HARDCODED_WHITELIST, ...config.envWhitelist].map((address) =>
    getAddress(address).toLowerCase(),
  ),
);

export function normalizeAddress(address: string): string {
  if (!isAddress(address)) {
    throw new Error(`Invalid address: ${address}`);
  }
  return getAddress(address);
}

export function assertWhitelistedAddress(address: string): void {
  const normalized = normalizeAddress(address).toLowerCase();
  if (!whitelist.has(normalized)) {
    throw new Error(`Address not allowed by executor whitelist: ${address}`);
  }
}

export function assertAmountWithinSingleTxCap(amountWei: bigint): void {
  const effectiveCap = config.singleTxCapWei < HARDCODED_SINGLE_TX_CAP_WEI ? config.singleTxCapWei : HARDCODED_SINGLE_TX_CAP_WEI;
  if (amountWei > effectiveCap) {
    throw new Error(
      `Amount exceeds single transaction cap (${effectiveCap.toString()} wei)`,
    );
  }
}
