import { getAddress, isAddress } from "ethers";

import { HARDCODED_SINGLE_TX_CAP_WEI } from "./config.js";

export function normalizeAddress(address: string): string {
  if (!isAddress(address)) {
    throw new Error(`Invalid address: ${address}`);
  }
  return getAddress(address);
}

export function assertWhitelistedAddress(address: string, whitelist: Set<string>): void {
  const normalized = normalizeAddress(address).toLowerCase();
  if (!whitelist.has(normalized)) {
    throw new Error(`Address not allowed by executor whitelist: ${address}`);
  }
}

export function assertAmountWithinSingleTxCap(
  amountWei: bigint,
  singleTxCapWei: bigint,
): void {
  const effectiveCap =
    singleTxCapWei < HARDCODED_SINGLE_TX_CAP_WEI ? singleTxCapWei : HARDCODED_SINGLE_TX_CAP_WEI;
  if (amountWei > effectiveCap) {
    throw new Error(
      `Amount exceeds single transaction cap (${effectiveCap.toString()} wei)`,
    );
  }
}

export function buildWhitelist(addresses: string[]): Set<string> {
  return new Set(addresses.map((address) => getAddress(address).toLowerCase()));
}
