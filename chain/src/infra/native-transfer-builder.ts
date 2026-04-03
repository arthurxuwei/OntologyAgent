import { parseUnits, type FeeData, type TransactionRequest } from "ethers";

export function buildNativeTransferRequest(args: {
  to: string;
  value: bigint;
  nonce: number;
  chainId: number;
  feeData: FeeData;
}): TransactionRequest {
  return {
    type: 2,
    to: args.to,
    value: args.value,
    nonce: args.nonce,
    chainId: args.chainId,
    gasLimit: 21_000n,
    maxFeePerGas: args.feeData.maxFeePerGas ?? parseUnits("20", "gwei"),
    maxPriorityFeePerGas: args.feeData.maxPriorityFeePerGas ?? parseUnits("2", "gwei"),
  };
}
