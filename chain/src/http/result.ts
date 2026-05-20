import type { Request, Response } from "express";
import { normalizeError } from "../domain/errors.js";

export async function sendResult<Result>(
  res: Response,
  handler: () => Promise<Result>,
): Promise<void> {
  try {
    res.status(200).json(await handler());
  } catch (error) {
    const normalized = normalizeError(error);
    res.status(400).json({
      error: {
        code: normalized.code,
        message: normalized.message,
        details: normalized.details,
      },
    });
  }
}

export function asyncRoute(
  handler: (req: Request, res: Response) => Promise<void>,
) {
  return (req: Request, res: Response) => {
    void handler(req, res);
  };
}
