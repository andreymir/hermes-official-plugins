# Fork notes: `hub-upstream`

This branch adds an opt-in gateway mode to `claude-subscription-directsdk`. With it, Hermes
reaches Claude through Andrei's CLIProxyAPI hub instead of `api.anthropic.com`. The user-facing
description is in [claude-subscription-directsdk/README.md](claude-subscription-directsdk/README.md#gateway-mode-fork-addition).
The other plugins in this repo are unchanged.

## Fork diff

Keep the diff to these files, so rebasing stays easy:

- `claude-subscription-directsdk/directsdk_setup.py`: `gateway()`, `apply_gateway()`, and gateway
  branches in `setup_status`, `discover_models` and `fetch_usage`.
- `claude-subscription-directsdk/directsdk.py`: applies the gateway after the inherited-override
  check, and uses a gateway-specific logged-out message.
- `claude-subscription-directsdk/plugin.yaml`: the `upstream_base_url` and `upstream_auth_env`
  settings. The version is `<upstream version>+gateway.<n>`.
- `claude-subscription-directsdk/README.md`: the "Gateway mode" section.
- `claude-subscription-directsdk/tests/test_directsdk_gateway.py`.
- This file.

## Deployment

- **Host:** Hermes host (`ssh tailscale-nl`), default profile.
- **Install:** pinned by commit with `--ref`.
- **Settings** in `~/.hermes/config.yaml`:
  - `plugins.entries.claude-subscription-directsdk-experimental.settings.upstream_base_url: https://h2c129dc896.lightson.top`
  - `...settings.upstream_auth_env: CLIPROXY_API_KEY`
  - `providers.claude-subscription-directsdk-experimental.stale_timeout_seconds: 900`
- **Hub requirement:** the hub must list `hermes-agent` in
  `upstream.claude.header-defaults.native-entrypoints`. See
  `unraid:docker-compose/cli-proxy-api/config.template.yaml`.
- **Login:** none on the Hermes host. The CLI authenticates with the hub key.

## Update procedure

1. Rebase on upstream (laptop, `~/hermes-official-plugins`):

   ```bash
   git switch hub-upstream
   git fetch upstream
   git rebase upstream/main
   ```

   Resolve conflicts so that the fork diff above stays small. If upstream changed the version in
   `plugin.yaml`, set it to `<new upstream version>+gateway.1`.

2. Run the plugin tests on the Hermes host. They need its Hermes checkout:

   ```bash
   rsync -a --delete --exclude .git claude-subscription-directsdk/ tailscale-nl:/tmp/csd-test/
   ssh tailscale-nl 'cd /tmp/csd-test && env -u CLIPROXY_API_KEY HERMES_AGENT_REPO=$HOME/.hermes/hermes-agent \
     timeout 900 setsid ~/.hermes/hermes-agent/venv/bin/python -m pytest -q -p no:cacheprovider tests \
     -k "not test_refusal_after_a_tool_call" > /tmp/csd-test.log 2>&1 < /dev/null; echo exit=$?; \
     tail -3 /tmp/csd-test.log; rm -rf /tmp/csd-test /tmp/csd-test.log'
   ```

   Expected: `exit=0` and no failures.
   - `env -u CLIPROXY_API_KEY` keeps the real hub key out of the tests.
   - `test_refusal_after_a_tool_call_reaches_the_user` is deselected because on this host it makes
     pytest exit silently with code 1. It does the same on an unpatched upstream checkout, so it is
     not caused by the fork. Recheck it after each rebase by running it alone. If it passes, drop
     the `-k` filter.

3. Push: `git push --force-with-lease origin hub-upstream`. Note the new commit SHA.

4. Install on the Hermes host. Back up the config first:

   ```bash
   ssh tailscale-nl 'cp -p ~/.hermes/config.yaml ~/.hermes/config.yaml.bak-$(date -u +%Y%m%dT%H%M%SZ) && \
     ~/.local/bin/hermes plugins install "andreymir/hermes-official-plugins#claude-subscription-directsdk" \
     --ref <new sha> --force --enable --yes-deps'
   ```

   The settings live in `config.yaml`, so a reinstall keeps them.

5. Verify:

   ```bash
   ssh tailscale-nl 'cd /tmp && ~/.local/bin/hermes chat --oneshot -Q -q "Reply with exactly: HUB-CLAUDE-OK" \
     --provider claude-subscription-directsdk-experimental -m sonnet'
   ssh tailscale-nl 'cd /tmp && ~/.local/bin/hermes chat --oneshot -Q -q "Reply with exactly: CODEX-DEFAULT-OK"'
   ```

   Then check Loki for a usage record with `client: hermes`, `provider: claude`
   (`{compose_service="usage-collector"} |= "hermes"`).

6. Keep Claude Code current. The plugin requires a minimum CLI version for each model (see the
   plugin README). Update it on the Hermes host with `~/.local/bin/claude update`.
