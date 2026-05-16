# Tencent Lighthouse Core Deployment

This deployment publishes and runs only the currently exposed core services:

- `ledger` on public port `8092`
- `circle` MCP on public port `8093`

It intentionally excludes `agent`, `x402-seller`, `x402-mock`, and the public
`chain` MCP service. The `chain` Docker image is still built because the Circle
MCP entrypoint lives in the same `chain/` package and starts with
`npm run start:circle`.

## Security Posture

This phase exposes MCP endpoints and the ledger management page directly to the
public internet. Use only sandbox/testnet credentials, set strict spend caps,
and keep real funds out of the configured wallets.

Before using mainnet funds or production Circle credentials, put these endpoints
behind authentication, IP allowlists, a VPN, or an identity-aware proxy.

## GitHub Secrets

Configure these repository secrets:

- `LIGHTHOUSE_HOST`: public IP or hostname of the Tencent Lighthouse instance.
- `LIGHTHOUSE_USER`: SSH user. Use a user that can write `/opt/ontologyagent`
  and run Docker without an interactive sudo prompt.
- `LIGHTHOUSE_SSH_KEY`: private SSH key for the deploy user.
- `GHCR_PAT`: optional token for server-side `docker login ghcr.io`. This is
  only needed when the GHCR packages are private or the Lighthouse server cannot
  pull anonymous images.
- `CLOUDFLARE_TUNNEL_TOKEN`: optional. Set this after creating a Cloudflare
  Tunnel for the domain; the deploy workflow will then start `cloudflared`
  alongside the core services.

The workflow uses the built-in `GITHUB_TOKEN` to push images to GHCR.

## Server Setup

Install Docker and the Docker Compose plugin on the Lighthouse instance, then
create the deployment directory:

```bash
mkdir -p /opt/ontologyagent/data/ledger /opt/ontologyagent/data/chain
```

Create `/opt/ontologyagent/.env` on the server. Do not commit this file.

```bash
cat > /opt/ontologyagent/.env <<'EOF'
CHAIN_MOCK=true

CIRCLE_API_KEY=
CIRCLE_ENTITY_SECRET=
CIRCLE_WALLET_SET_ID=
CIRCLE_USDC_TOKEN_ID=

LEDGER_SETTLEMENT_ENABLED=false
LEDGER_SETTLEMENT_MCP_URL=http://circle-mcp:8093/mcp/
LEDGER_SETTLEMENT_REQUIRE_SUCCESS=false
EOF
```

For safer smoke testing, keep `CHAIN_MOCK=true`. Switch to `CHAIN_MOCK=false`
only after the wallet contains testnet funds you are prepared to spend.

## Tencent Lighthouse Firewall

Open the temporary public test ports:

- TCP `22` for SSH
- TCP `8092` for ledger UI, ledger health, and ledger MCP
- TCP `8093` for circle MCP

Example `tccli` commands:

```bash
tccli lighthouse DescribeInstances --region <region> --Limit 20
tccli lighthouse DescribeFirewallRules --region <region> --InstanceId <instance-id>
tccli lighthouse CreateFirewallRules --region <region> \
  --InstanceId <instance-id> \
  --FirewallRules.0.Protocol TCP \
  --FirewallRules.0.Port 8092 \
  --FirewallRules.0.CidrBlock 0.0.0.0/0 \
  --FirewallRules.0.Action ACCEPT \
  --FirewallRules.0.FirewallRuleDescription ontology-ledger \
  --FirewallRules.1.Protocol TCP \
  --FirewallRules.1.Port 8093 \
  --FirewallRules.1.CidrBlock 0.0.0.0/0 \
  --FirewallRules.1.Action ACCEPT \
  --FirewallRules.1.FirewallRuleDescription ontology-circle-mcp
```

Keep `80` and `443` closed until a later Nginx/domain phase.

## Optional Cloudflare Domain Handoff

After the domain is active in Cloudflare, prefer Cloudflare Tunnel over opening
the MCP ports directly. Create a remotely managed tunnel in Cloudflare Zero
Trust, then add public hostnames that target the Docker service names:

- `ledger.<domain>` -> `http://ledger:8092`
- `circle-mcp.<domain>` -> `http://circle:8093`

Copy the tunnel token into the GitHub repository secret
`CLOUDFLARE_TUNNEL_TOKEN`. On the next deploy, the workflow copies
`docker-compose.cloudflare.yml`, writes the token into
`/opt/ontologyagent/.env.deploy`, and includes the `cloudflared` service in the
Compose command.

Once the Cloudflare hostnames respond, remove the naked public exposure from the
Lighthouse firewall:

- Keep TCP `22` open for SSH from admin networks.
- Close TCP `8092` and `8093` to the public internet.
- Keep TCP `80` and `443` closed unless you later add an on-box reverse proxy.

Validate through Cloudflare:

```bash
curl -fsS https://ledger.<domain>/health
curl -fsS https://circle-mcp.<domain>/mcp/ \
  -H 'Content-Type: application/json' \
  -H 'Accept: application/json, text/event-stream' \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"smoke-test","version":"0.1.0"}}}'
```

## CI/CD Flow

On push to `main`, `.github/workflows/deploy-core-services.yml`:

1. Runs ledger unit tests.
2. Runs chain package typecheck and tests because Circle MCP is implemented in
   the same package.
3. Builds `ledger` and `chain` images. The `chain` image contains both the
   chain and Circle MCP entrypoints, but only Circle is deployed for now.
4. Pushes both images to GHCR with `<sha>` and `latest` tags.
5. Copies `docker-compose.core.yml` and `docker-compose.cloudflare.yml` to
   `/opt/ontologyagent`.
6. Writes `/opt/ontologyagent/.env.deploy` with the image prefix and SHA tag.
7. Pulls and restarts the core services with Docker Compose. If
   `CLOUDFLARE_TUNNEL_TOKEN` is configured, it also starts `cloudflared`.

The `circle` service reuses the `chain` image and starts with
`npm run start:circle`. The `chain` MCP container is not started.

## Validation

From your local machine:

```bash
curl -fsS http://<lighthouse-ip>:8092/health
```

List MCP tools:

```bash
curl -fsS http://<lighthouse-ip>:8092/mcp/ \
  -H 'Content-Type: application/json' \
  -H 'Accept: application/json, text/event-stream' \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"smoke-test","version":"0.1.0"}}}'

curl -fsS http://<lighthouse-ip>:8093/mcp/ \
  -H 'Content-Type: application/json' \
  -H 'Accept: application/json, text/event-stream' \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"smoke-test","version":"0.1.0"}}}'
```

Verify persistence on the server:

```bash
cd /opt/ontologyagent
docker compose --env-file .env --env-file .env.deploy -f docker-compose.core.yml restart ledger
curl -fsS http://127.0.0.1:8092/ledger/state
```

Inspect deployment state:

```bash
cd /opt/ontologyagent
docker compose --env-file .env --env-file .env.deploy -f docker-compose.core.yml ps
docker compose --env-file .env --env-file .env.deploy -f docker-compose.core.yml logs --tail=100 ledger
docker compose --env-file .env --env-file .env.deploy -f docker-compose.core.yml logs --tail=100 circle
```
