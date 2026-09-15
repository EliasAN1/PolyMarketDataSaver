# Launch pmtrader on Ubuntu (AWS EC2)

This walks a **private** clone of `EliasAN1/PolyMarketDataSaver` from a blank Ubuntu EC2 box through a live `systemd` trader. The bot and the UI (port **3848**) run in **one** process: `python -m pmtrader`.

Use **Ubuntu 24.04 LTS**. It ships Python 3.12. Ubuntu 22.04’s system Python is 3.10 and is too old (`pmtrader` needs **3.11+**).

A `t3.small` (2 vCPU, 2 GB) is plenty. `t3.micro` can work if nothing else is on the box.

Do **not** open port 3848 (or 22 from `0.0.0.0/0` if you can avoid it) on the public internet. The UI talks to your wallet APIs. Reach it with an SSH tunnel or Tailscale.

---

## 1. Create the instance

1. AWS Console → **EC2 → Launch instance**.
2. Name it (e.g. `centionaire`).
3. AMI: **Ubuntu Server 24.04 LTS**.
4. Instance type: `t3.small`.
5. Key pair: create or pick an existing `.pem`. Download it and keep it private.
6. Network:
   - VPC/subnet with a public IP (or a Tailscale-only box with no public IP — then skip the SSH-from-laptop step and use Tailscale SSH).
   - Security group inbound:
     - **SSH (22)** from **your IP only** (`/32`).
     - No HTTP/HTTPS/3848 unless you know why you need it.
7. Storage: 20 GB gp3 is enough.
8. Launch. Copy the public IPv4 (or the Tailscale IP later).

On your PC, lock down the key:

```bash
chmod 400 /path/to/your-key.pem
```

SSH in (replace host and key):

```bash
ssh -i /path/to/your-key.pem ubuntu@YOUR_EC2_PUBLIC_IP
```

First login:

```bash
sudo apt-get update
sudo apt-get upgrade -y
sudo timedatectl set-timezone America/New_York
```

`config.toml` session filters (`wall`, `tokyo_open`, `london`, …) use the **machine’s local clock**. Set the timezone to the same one you used on Windows or those windows will shift.

---

## 2. Let the instance clone a private GitHub repo

Pick one method.

### Option A — GitHub deploy key (best for a bot box)

On the instance:

```bash
ssh-keygen -t ed25519 -C "ec2-pmtrader" -f ~/.ssh/github_deploy -N ""
cat ~/.ssh/github_deploy.pub
```

On GitHub: repo **Settings → Deploy keys → Add deploy key**. Paste the public key. Read-only is enough. Name it `ec2-pmtrader`.

Then:

```bash
cat >> ~/.ssh/config << 'EOF'
Host github.com
  HostName github.com
  User git
  IdentityFile ~/.ssh/github_deploy
  IdentitiesOnly yes
EOF
chmod 600 ~/.ssh/config
ssh -T git@github.com
```

You want: `Hi EliasAN1! You've successfully authenticated...`

### Option B — GitHub CLI + token

```bash
sudo apt-get install -y gh
gh auth login
```

HTTPS, login by token, paste a classic PAT with `repo` scope (GitHub → Settings → Developer settings → Personal access tokens).

---

## 3. Install system packages and clone

```bash
sudo apt-get install -y git python3 python3-venv python3-pip python3-dev build-essential
python3 --version   # must be 3.11 or 3.12
```

Clone (SSH if you used a deploy key):

```bash
cd ~
git clone git@github.com:EliasAN1/PolyMarketDataSaver.git
cd PolyMarketDataSaver
```

HTTPS if you used `gh auth`:

```bash
gh repo clone EliasAN1/PolyMarketDataSaver
cd PolyMarketDataSaver
```

---

## 4. Install the trader

All commands from here on run **inside `trader/`**. That directory is the working dir for `.env`, `config.toml`, and `logs/`.

```bash
cd ~/PolyMarketDataSaver/trader
python3 -m venv .venv
source .venv/bin/activate
pip install -U pip setuptools wheel
pip install -e .
```

`python -c "import pmtrader; print('ok')"` should print `ok`.

---

## 5. Secrets (`.env`)

`.env` is gitignored. Never commit it. Copy the template and edit it **on the instance** (or `scp` your existing Windows `trader/.env`).

```bash
cp .env.example .env
nano .env
chmod 600 .env
```

Fill at least:

| Variable | What it is |
| --- | --- |
| `POLYMARKET_PRIVATE_KEY` | Signer key (hex, `0x` optional) |
| `POLYMARKET_FUNDER` | Deposit / proxy address that holds **pUSD** |
| `POLYMARKET_WALLET_ADDRESS` | Same as funder unless you know otherwise |
| `POLYMARKET_SIGNATURE_TYPE` | `3` for a Polymarket deposit wallet |
| `POLYMARKET_CHAIN_ID` | `137` (Polygon) |

To redeem settled winners from the UI / `close-positions`, also set builder keys from polymarket.com → Settings → Builder:

```
POLYMARKET_BUILDER_API_KEY=
POLYMARKET_BUILDER_SECRET=
POLYMARKET_BUILDER_PASSPHRASE=
```

Leave `UI_HOST` unset (defaults to `127.0.0.1`) so the dashboard is not on the public NIC.

Copy from your PC instead of typing keys:

```powershell
scp -i C:\path\to\your-key.pem C:\Users\Elias\Desktop\Programming\PolyMarketDataSaver\trader\.env ubuntu@YOUR_EC2_PUBLIC_IP:~/PolyMarketDataSaver/trader/.env
```

Then on the instance: `chmod 600 ~/PolyMarketDataSaver/trader/.env`

---

## 6. Strategy config

Edit `config.toml` in `trader/` (stake, odds band, sessions, …). It is already in git; change it on the instance if this box should differ from your laptop.

---

## 7. Smoke test (paper, then one live attach)

Still in `trader/` with the venv active:

```bash
source ~/PolyMarketDataSaver/trader/.venv/bin/activate
cd ~/PolyMarketDataSaver/trader
mkdir -p logs
python -m pmtrader --dry-run
```

You should see feeds connecting and `UI http://127.0.0.1:3848`. Ctrl+C to stop.

On your **laptop** (leave the dry-run running), tunnel the UI:

```bash
ssh -i /path/to/your-key.pem -L 3848:127.0.0.1:3848 ubuntu@YOUR_EC2_PUBLIC_IP
```

Open [http://127.0.0.1:3848](http://127.0.0.1:3848) locally. If the radar and wallet look right, stop the dry-run and go live with systemd.

---

## 8. Run it as a service (survives SSH disconnect and reboot)

```bash
sudo tee /etc/systemd/system/pmtrader.service >/dev/null << 'EOF'
[Unit]
Description=Polymarket BTC 5m live trader
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=ubuntu
Group=ubuntu
WorkingDirectory=/home/ubuntu/PolyMarketDataSaver/trader
EnvironmentFile=/home/ubuntu/PolyMarketDataSaver/trader/.env
ExecStart=/home/ubuntu/PolyMarketDataSaver/trader/.venv/bin/python -m pmtrader --config config.toml --env .env --log-file logs/trades.jsonl
Restart=always
RestartSec=5
TimeoutStopSec=30

# Do not dump keys into journal from extra env files
NoNewPrivileges=true

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable --now pmtrader
sudo systemctl status pmtrader --no-pager
```

Logs:

```bash
journalctl -u pmtrader -f
```

Fills also append to `~/PolyMarketDataSaver/trader/logs/trades.jsonl`.

Stop / restart:

```bash
sudo systemctl stop pmtrader
sudo systemctl restart pmtrader
```

---

## 9. Open the UI (SSH tunnel or Tailscale)

### SSH tunnel (simplest)

From your PC, whenever you want the dashboard:

```bash
ssh -i /path/to/your-key.pem -N -L 3848:127.0.0.1:3848 ubuntu@YOUR_EC2_PUBLIC_IP
```

Then [http://127.0.0.1:3848](http://127.0.0.1:3848). `-N` means “tunnel only, no shell”.

### Tailscale (phone app / persistent URL)

On the instance:

```bash
curl -fsSL https://tailscale.com/install.sh | sh
sudo tailscale up
sudo tailscale serve --bg 3848
sudo tailscale serve status
```

Sign in with the **same** Tailscale account as your phone. The printed `https://….ts.net/` URL is what the Android Centionaire app uses (Server setting). Keep `UI_HOST=127.0.0.1` so Serve is the only way in.

---

## 10. Pull updates later

```bash
sudo systemctl stop pmtrader
cd ~/PolyMarketDataSaver
git pull
cd trader
source .venv/bin/activate
pip install -e .
sudo systemctl start pmtrader
sudo systemctl status pmtrader --no-pager
```

`.env` and `logs/` stay on the box; they are not in git.

---

## 11. Close / redeem positions

From `trader/` with the venv (stop the live service first if it holds the same CLOB session and the command fights it — usually you can scan while live):

```bash
cd ~/PolyMarketDataSaver/trader
source .venv/bin/activate
python -m pmtrader close-positions --scan-only
python -m pmtrader close-positions
```

Or use **Wallet → Scan queue / Redeem & sell all** in the UI.

---

## 12. If something fails

| Symptom | Check |
| --- | --- |
| `Config not found` | `WorkingDirectory` must be `…/trader` |
| `Live mode needs POLYMARKET_PRIVATE_KEY` | `.env` path, `chmod 600`, no quotes/spaces around the key unless the value itself needs them |
| `requires-python >= 3.11` | You are on Ubuntu 22.04 — use 24.04 or install 3.12 from deadsnakes |
| Service exits immediately | `journalctl -u pmtrader -n 80 --no-pager` |
| UI blank / offline radar | Trader process is down, or you are hitting the wrong host; tunnel to `127.0.0.1:3848` on the instance |
| Sessions never fire | Timezone (`timedatectl`) vs `sessions` in `config.toml` |
| GitHub `Permission denied` | Deploy key is for **this** repo, `IdentityFile` points at it, and `ssh -T git@github.com` works |

---

## One-page recap

```bash
# once, on Ubuntu 24.04
sudo apt-get update && sudo apt-get install -y git python3 python3-venv python3-pip python3-dev build-essential
git clone git@github.com:EliasAN1/PolyMarketDataSaver.git
cd PolyMarketDataSaver/trader
python3 -m venv .venv && source .venv/bin/activate && pip install -U pip && pip install -e .
cp .env.example .env && nano .env && chmod 600 .env
mkdir -p logs
python -m pmtrader --dry-run          # then Ctrl+C
sudo systemctl enable --now pmtrader  # after installing the unit in section 8
```
