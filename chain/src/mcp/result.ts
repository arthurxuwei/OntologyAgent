import { normalizeError } from "../domain/errors.js";

type StructuredPayload = Record<string, unknown>;

function structuredText(value: unknown): string {
  return JSON.stringify(value, null, 2);
}

function successResult(result: StructuredPayload) {
  return {
    content: [
      {
        type: "text" as const,
        text: structuredText(result),
      },
    ],
    structuredContent: result,
  };
}

function errorResult(error: unknown) {
  const normalized = normalizeError(error);
  const payload = {
    error: {
      code: normalized.code,
      message: normalized.message,
      details: normalized.details,
    },
  };

  return {
    content: [
      {
        type: "text" as const,
        text: structuredText(payload),
      },
    ],
    structuredContent: payload,
    isError: true,
  };
}

export async function runTool<Result>(
  handler: () => Promise<Result>,
): Promise<ReturnType<typeof successResult> | ReturnType<typeof errorResult>> {
  try {
    return successResult((await handler()) as StructuredPayload);
  } catch (error) {
    return errorResult(error);
  }
}
