from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

app = FastAPI(title="OntologyAgent brain-py")


@app.get("/health")
def health() -> dict[str, str]:
    return {"service": "OntologyAgent-brain-py", "status": "ok"}


@app.post("/mock-x402")
def mock_x402(request: Request):
    payment_tx_hash = request.headers.get("x-payment-tx-hash")
    if not payment_tx_hash:
        return JSONResponse(
            status_code=402,
            content={
                "error": "payment_required",
                "message": "send on-chain payment then retry",
            },
        )

    return {
        "ok": True,
        "accepted_payment_tx_hash": payment_tx_hash,
        "quote": {"tokenIn": "ETH", "tokenOut": "USDC", "price": "demo"},
    }
