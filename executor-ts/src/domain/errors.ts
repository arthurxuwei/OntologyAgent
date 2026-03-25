import { ZodError } from "zod";

export type ErrorCode =
  | "VALIDATION_ERROR"
  | "CONFIG_ERROR"
  | "POLICY_VIOLATION"
  | "X402_PROTOCOL_ERROR"
  | "UPSTREAM_402_EXHAUSTED"
  | "UPSTREAM_REQUEST_FAILED"
  | "NETWORK_MISMATCH"
  | "SIGNER_UNAVAILABLE"
  | "BUNDLER_ERROR"
  | "INTERNAL_ERROR";

export class AppError extends Error {
  constructor(
    public readonly code: ErrorCode,
    message: string,
    public readonly statusCode = 400,
    public readonly details?: unknown,
  ) {
    super(message);
    this.name = "AppError";
  }
}

export function normalizeError(error: unknown): AppError {
  if (error instanceof AppError) {
    return error;
  }

  if (error instanceof ZodError) {
    return new AppError("VALIDATION_ERROR", "Request validation failed", 400, error.flatten());
  }

  if (error instanceof Error) {
    return new AppError("INTERNAL_ERROR", error.message, 500);
  }

  return new AppError("INTERNAL_ERROR", "Unknown error", 500);
}
