<div align="center">

# `hm-api` ⚡

**DevEco Code OpenAI-compatible API CLI**

[![Python](https://img.shields.io/badge/Python-3.12%2B-blue?logo=python)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115%2B-009688?logo=fastapi)](https://fastapi.tiangolo.com/)
[![uv](https://img.shields.io/badge/uv-powered-8A2BE2?logo=astral)](https://docs.astral.sh/uv/)
[![License](https://img.shields.io/badge/License-AGPL--3.0%20%2B%20Non--Commercial-red)](LICENSE)

<p align="center">
  <strong><code>login</code> · <code>serve</code> · <code>status</code></strong>
</p>

</div>

---

## ✨ Features

- **OpenAI-compatible** — `/v1/models` and `/v1/chat/completions`
- **Streaming & non-streaming** — automatic SSE forwarding and `/no-stream` fallback
- **Built-in auth** — optional `--key` API key protection
- **Proxy support** — pass upstream HTTP/HTTPS proxy to `httpx`
- **Multi-account management** — add, switch, and delete DevEco accounts in a web UI
- **Encrypted credentials** — local account tokens stored safely under `./cred`
- **Admin UI** — password-protected React + shadcn/ui console at `/admin`
- **Call logs** — local metadata logs for model and chat completion requests
- **Async powered** — FastAPI + `httpx` + `uvloop`

---

## 🚀 Quick Start

```bash
# 1. clone
git clone https://github.com/Max65588/de2api.git
cd de2api

# 2. install dependencies (requires uv)
uv sync
cd web
npm install
npm run build
cd ..

# 3. login via DevEco OAuth
uv run hm-api login

# 4. create local runtime config
uv run hm-api init-config

# 5. serve the OpenAI-compatible API and admin UI
uv run hm-api serve --host 0.0.0.0 --port 8000
```

> If you prefer not to open the browser automatically, use `uv run hm-api login --no-browser` and follow the printed URL.
> To add the first account from the web UI, run `uv run hm-api init-config`, start `serve`, and open `http://localhost:8000/admin`.
> When the admin UI is running on a remote server, DevEco may redirect your local browser to `http://localhost:<port>/callback`.
> If that page does not continue automatically, copy the full callback URL from the address bar and paste it into the admin UI dialog to finish adding the account.
> For automatic remote callbacks, expose the same server port through SSH local forwarding, for example `ssh -L 8202:127.0.0.1:8202 user@server`, then open the admin UI and add the account from the same local browser.
> If your admin UI is behind Nginx or HTTPS and DevEco chooses the wrong local callback port, set `HM_API_OAUTH_CALLBACK_PORT=8202` before starting `hm-api serve`.

---

## 📖 Commands

<div align="center">

| Command | Description |
|---------|-------------|
| `hm-api login [--proxy PROXY] [--no-browser]` | Authenticate with DevEco Code |
| `hm-api init-config [--force]` | Create `cred/config.json` with admin password and API key |
| `hm-api export-accounts FILE` | Export logged-in DevEco accounts for migration |
| `hm-api import-accounts FILE` | Import exported DevEco accounts on another machine |
| `hm-api serve [--host HOST] [--port PORT] [--proxy PROXY]` | Start the OpenAI-compatible proxy server and admin UI |
| `hm-api status` | Show current login status |

</div>

### `serve` options

| Option | Default | Description |
|--------|---------|-------------|
| `--host` | `127.0.0.1` | Bind host |
| `--port` | `8000` | Bind port |
| `--proxy` | — | Upstream HTTP/HTTPS proxy |
| `--key` | — | Compatibility override: add one CLI API key named `cli` |
| `--admin-password` | — | Compatibility override for the web admin UI password |

### Remote admin OAuth callback

DevEco's OAuth page only accepts a callback port. After login it redirects the browser to `http://localhost:<port>/callback`, where `localhost` means the user's local machine, not the remote server.

You can finish adding an account in either way:

- Manual callback: after DevEco redirects to `http://localhost:<port>/callback?...`, copy the full URL from the browser address bar, return to the admin UI dialog, paste it, and click finish.
- SSH local forwarding: start the server on the remote machine, then run `ssh -L 8202:127.0.0.1:8202 user@server` on your local machine. Open the admin UI from the same local browser. If needed, start the server with `HM_API_OAUTH_CALLBACK_PORT=8202` so DevEco always redirects to local port `8202`.
- Account migration in the admin UI: after logging in locally, open `/admin`, click `导出账户`, then open the server admin UI and click `导入账户` to upload the JSON file.
- Account migration with CLI: login locally with `uv run hm-api login`, export with `uv run hm-api export-accounts accounts-export.json`, upload that file to the server, then run `uv run hm-api import-accounts accounts-export.json` on the server.

The exported account file contains credentials. Delete it after importing.

---

## 🔌 Usage Example

```bash
# list available models
curl http://localhost:8000/v1/models \
  -H "Authorization: Bearer your-secret-key"

# chat completion (non-streaming)
curl http://localhost:8000/v1/chat/completions \
  -H "Authorization: Bearer your-secret-key" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "GLM-5.1",
    "messages": [{"role": "user", "content": "Hello!"}]
  }'
```

To use a specific saved account instead of the active account, pass the account id copied from the admin UI:

```bash
curl http://localhost:8000/v1/models \
  -H "Authorization: Bearer your-secret-key" \
  -H "x-hm-account-id: your-account-id"
```

---

## 🛡️ Credentials

All authentication data is encrypted and stored locally under `./cred`.
Multiple DevEco accounts are saved in `cred/auth.json`; the active account is used by default.
API call metadata is saved in `cred/calls.jsonl` and displayed in the admin UI. Message content is not logged.
Runtime configuration is stored in `cred/config.json`.

Example `cred/config.json` with multiple API keys, account strategy, and default theme:

```json
{
  "admin_password": "change-this-admin-password",
  "account_strategy": "sticky",
  "theme": "dark",
  "api_keys": [
    {
      "name": "desktop",
      "key": "sk-desktop-client-secret",
      "enabled": true
    },
    {
      "name": "server",
      "key": "sk-server-client-secret",
      "enabled": true
    }
  ]
}
```

The admin UI call logs show the matched `api_key_name` for each valid API request.
API keys can be created in the admin UI with a random `sk-...` value or a custom value.
`account_strategy` supports `sticky` and `round_robin`; explicit `x-hm-account-id` always takes priority.
`theme` supports `dark` and `light`; the default generated config uses `dark`.

<div align="center">

⚠️ **Never commit the `cred/` directory.** It is already ignored by `.gitignore`.

</div>

---

## 📦 Project Structure

```text
de2api/
├── src/hm_api/          # CLI and server source code
│   ├── cli.py           # Typer CLI entry
│   ├── server.py        # FastAPI OpenAI-compatible proxy
│   ├── login.py         # DevEco OAuth login flow
│   ├── accounts.py      # Multi-account credential store
│   ├── crypto.py        # Credential encryption
│   ├── config.py        # Constants and defaults
│   └── static/          # React admin UI
├── web/                 # React + shadcn/ui admin frontend source
├── pyproject.toml       # Project metadata and dependencies
├── uv.lock              # Locked dependency tree
├── LICENSE              # AGPL-3.0 + Non-Commercial clause
└── README.md            # This file
```

---

## 📜 License

<div align="center">

This project is licensed under **AGPL-3.0 with additional Non-Commercial restrictions**.

[![License: AGPL v3](https://img.shields.io/badge/License-AGPL%20v3-blue.svg)](https://www.gnu.org/licenses/agpl-3.0)

</div>

You may use, modify, and distribute this software **for non-commercial purposes only**.
Commercial use — including but not limited to selling, offering paid services, or
incorporating it into commercial products — is **strictly prohibited**.

See [LICENSE](LICENSE) for full terms.

---

<div align="center">

Maintained by <a href="https://github.com/Max65588">Max65588</a>

and Thanks to the Linux.do community

</div>
