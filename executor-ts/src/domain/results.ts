import type { AppError } from "./errors.js";

export type ApiMeta = {
  requestId: string;
};

export type SuccessResponse<T> = {
  ok: true;
  result: T;
  meta: ApiMeta;
};

export type ErrorResponse = {
  ok: false;
  error: {
    code: string;
    message: string;
    details?: unknown;
  };
  meta: ApiMeta;
};

export function successResponse<T>(requestId: string, result: T): SuccessResponse<T> {
  return {
    ok: true,
    result,
    meta: { requestId },
  };
}

export function errorResponse(requestId: string, error: AppError): ErrorResponse {
  return {
    ok: false,
    error: {
      code: error.code,
      message: error.message,
      details: error.details,
    },
    meta: { requestId },
  };
}
