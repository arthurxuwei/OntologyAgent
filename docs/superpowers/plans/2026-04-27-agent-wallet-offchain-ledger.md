# Agent Wallet Offchain Ledger Service Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an independent Dockerized JSON-backed offchain ledger service for Agent Wallet balances and escrow flows.

**Architecture:** Keep accounting rules and persistence in `ledger/main.py`. Persist ledger state in `LEDGER_STATE_PATH`. Expose HTTP endpoints from the ledger service and wire it into `docker-compose.yml`.

**Tech Stack:** Python, Pydantic, FastAPI, unittest, existing JSON state store.

---

## File Map

- Create `ledger/main.py`: FastAPI service, domain models, amount validation, balance mutation rules, escrow state machine, JSON persistence.
- Create `ledger/tests/test_ledger_service.py`: API and persistence tests.
- Create `ledger/requirements.txt`: FastAPI and Uvicorn dependencies.
- Create `ledger/Dockerfile`: container entrypoint.
- Modify `docker-compose.yml`: add `ledger` service and persistent `ledger/data` mount.

## Tasks

### Task 1: Ledger Service

- [ ] Write failing API tests for health, credit, escrow lock, release, refund, insufficient funds, invalid transitions, and JSON persistence.
- [ ] Implement `ledger/main.py` with `LedgerState`, `LedgerAccount`, `LedgerEntry`, `EscrowRecord`, and store helpers.
- [ ] Run `cd ledger && python -m unittest tests.test_ledger_service`.

### Task 2: Docker Service

- [ ] Add `ledger/requirements.txt`.
- [ ] Add `ledger/Dockerfile`.
- [ ] Add `ledger` to `docker-compose.yml` with `LEDGER_STATE_PATH=/app/data/offchain_ledger.json` and `./ledger/data:/app/data`.

### Task 3: Verification

- [ ] Run `cd ledger && python -m unittest`.
- [ ] Run `git diff --check`.
