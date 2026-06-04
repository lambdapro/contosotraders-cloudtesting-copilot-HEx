# Agentic STLC — HyperExecute & the runner-local app (known limitation)

## Summary

The Kane stage (Stage 1) verifies requirements against the app on the GitHub
Actions runner's `localhost:3000` and works correctly. The **HyperExecute
regression stage cannot reach that runner-local app** — its cloud Playwright
browser sessions are initiated from HE VMs (separate US-EAST-1 cloud machines)
and cannot bind a tunnel back to the runner's localhost.

To run the HyperExecute regression at scale, point it at a **public URL** (no
tunnel). For app states that exist only on the local dev build (e.g. the
"Memorial Day Sale" banner), rely on Kane Stage 1 for the functional verdict.

## Evidence (June 2026, branch `feature/agentic-stlc-pipeline`)

`chromium.connect()` to `wss://cdp.lambdatest.com/playwright` from an HE VM:

| Tunnel configuration | Result at `connect()` |
|---|---|
| HE-managed global tunnel (`tunnel: true` + `tunnelOpts.global`, empty `tunnelName`) | ❌ `browserType.connect: Browser/Target has been closed` |
| Named runner tunnel (Kane model — `LT.exe --tunnelName contoso-he-N` on the runner, name propagated to caps via `LT_TUNNEL_NAME`, **verified** via debug echo `DEBUG_TUNNEL=[contoso-he-38]`) | ❌ `400 … either tunnel is not running or disconnected` |
| Local Windows manual tunnel | ❌ `400 … either tunnel is not running or disconnected` |
| **`tunnel: false` against a PUBLIC URL** (isolation test) | ✅ **connects, navigates, returns title** |

### Why Kane works but HyperExecute does not

Kane runs **on the runner**, alongside a named tunnel (`contoso-kane-N`,
`KANE_TUNNEL_NAME`); its cloud browser session is initiated from the same machine
that hosts the tunnel and app. HyperExecute fans tests out to **separate cloud
VMs** that have no such locality, so their cloud Playwright sessions cannot bind
the runner's tunnel.

## How to run HyperExecute regression at scale

1. Deploy the Contoso UI to a public URL (see `iac/` / `docs/deployment-instructions.md`).
2. Set `REACT_APP_BASEURLFORPLAYWRIGHTTESTING=<public-url>` in the orchestrate
   job env (lambda.setup.ts uses it as the Playwright `baseURL`).
3. Remove the runner tunnel start; run with no tunnel in the caps.

The cloud Playwright sessions then reach the public app directly — the only
configuration in which `connect()` succeeds.
