# Tencent Lighthouse Core Deployment

This deployment publishes and runs only the currently exposed core services:

- `ledger` on public port `8092`

It intentionally excludes `agent`, `x402-seller`, `x402-mock`, and public
`chain`/`circle` endpoints. The `chain` Docker image is still built
because the internal Circle REST entrypoint lives in the same `chain/` package and starts with
`npm run start:circle`.

## Security Posture

This phase exposes only the ledger UI and ledger REST API directly to the
public internet. Circle REST is an internal backend used by ledger for wallet
onboarding and escrow release settlement. Use only sandbox/testnet credentials,
set strict spend caps, and keep real funds out of the configured wallets.

Before using mainnet funds or production Circle credentials, put these endpoints
behind authentication, IP allowlists, a VPN, or an identity-aware proxy.

## GitHub Secrets

Configure these repository secrets:

- `LIGHTHOUSE_HOST`: public IP or hostname of the Tencent Lighthouse instance.
- `LIGHTHOUSE_USER`: SSH user. Use a user that can write `/opt/kovaloop`
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
mkdir -p /opt/kovaloop/data/ledger /opt/kovaloop/data/chain
```

Create `/opt/kovaloop/.env` on the server. Do not commit this file.

```bash
cat > /opt/kovaloop/.env <<'EOF'
CHAIN_MOCK=true

CIRCLE_API_KEY=
CIRCLE_ENTITY_SECRET=
CIRCLE_WALLET_SET_ID=
CIRCLE_USDC_TOKEN_ID=

LEDGER_SETTLEMENT_ENABLED=true
LEDGER_SETTLEMENT_HTTP_URL=http://circle:8093
LEDGER_SETTLEMENT_REQUIRE_SUCCESS=true
LEDGER_WALLET_HTTP_URL=http://circle:8093
EOF
```

For safer smoke testing, keep `CHAIN_MOCK=true`. Switch to `CHAIN_MOCK=false`
only after the wallet contains testnet funds you are prepared to spend.

## Tencent Lighthouse Firewall

Open the temporary public test ports:

- TCP `22` for SSH
- TCP `8092` for ledger UI, ledger health, and ledger REST API

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
  --FirewallRules.0.FirewallRuleDescription kovaloop-ledger
```

Keep `80` and `443` closed until a later Nginx/domain phase.

## Optional Cloudflare Domain Handoff

After the domain is active in Cloudflare, prefer Cloudflare Tunnel over opening
service ports directly. Create a remotely managed tunnel in Cloudflare Zero
Trust, then add public hostnames that target the Docker service names:

- `ledger.<domain>` -> `http://ledger:8092`

Copy the tunnel token into the GitHub repository secret
`CLOUDFLARE_TUNNEL_TOKEN`. On the next deploy, the workflow copies
`docker-compose.cloudflare.yml`, writes the token into
`/opt/kovaloop/.env.deploy`, and includes the `cloudflared` service in the
Compose command.

Once the Cloudflare hostnames respond, remove the naked public exposure from the
Lighthouse firewall:

- Keep TCP `22` open for SSH from admin networks.
- Close TCP `8092` to the public internet after Cloudflare is serving ledger.
- Keep TCP `80` and `443` closed unless you later add an on-box reverse proxy.

Validate through Cloudflare:

```bash
curl -fsS https://ledger.<domain>/health
```

## CI/CD Flow

On push to `main`, `.github/workflows/deploy-core-services.yml`:

1. Runs ledger unit tests.
2. Runs chain package typecheck and tests because Circle REST is implemented in
   the same package.
3. Builds `ledger` and `chain` images. The `chain` image contains both the
   chain and Circle REST entrypoints, but only internal Circle is deployed for now.
4. Pushes both images to GHCR with `<sha>` and `latest` tags.
5. Copies `docker-compose.core.yml` and `docker-compose.cloudflare.yml` to
   `/opt/kovaloop`.
6. Writes `/opt/kovaloop/.env.deploy` with the image prefix and SHA tag.
7. Pulls and restarts the core services with Docker Compose. If
   `CLOUDFLARE_TUNNEL_TOKEN` is configured, it also starts `cloudflared`.

The internal `circle` service reuses the `chain` image and starts with
`npm run start:circle`. It is not published as a public endpoint. The `chain`
container is not started in this core deployment.

## Validation

From your local machine:

```bash
curl -fsS http://<lighthouse-ip>:8092/health
```

Read ledger state:

```bash
curl -fsS http://<lighthouse-ip>:8092/ledger/state
```

Verify persistence on the server:

```bash
cd /opt/kovaloop
docker compose --env-file .env --env-file .env.deploy -f docker-compose.core.yml restart ledger
curl -fsS http://127.0.0.1:8092/ledger/state
```

Inspect deployment state:

```bash
cd /opt/kovaloop
docker compose --env-file .env --env-file .env.deploy -f docker-compose.core.yml ps
docker compose --env-file .env --env-file .env.deploy -f docker-compose.core.yml logs --tail=100 ledger
docker compose --env-file .env --env-file .env.deploy -f docker-compose.core.yml logs --tail=100 circle
```
