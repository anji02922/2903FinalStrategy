# Azure Deployment Guide — ETH/USDT Scalping Bot

Complete step-by-step guide for someone brand new to Azure.

---

## PART 1: Create Azure Account & Subscription

### Step 1.1 — Create a free Azure account

1. Go to **https://azure.microsoft.com/free**
2. Click **"Start free"**
3. Sign in with your Microsoft account (or create one)
4. You'll need:
   - A phone number (for verification)
   - A credit/debit card (for identity verification — you won't be charged during free trial)
5. You get **$200 free credit for 30 days** + 12 months of free services

> **Cost after free trial**: The B1s VM costs **~$7.59/month**. Azure will NOT auto-charge you — you must explicitly upgrade from free to pay-as-you-go.

### Step 1.2 — Upgrade to Pay-As-You-Go (when free trial ends)

1. Go to **https://portal.azure.com**
2. Search for **"Subscriptions"** in the top search bar
3. Click your subscription → **"Upgrade"**
4. Select **"Pay-As-You-Go"** plan
5. Your card will be charged monthly based on usage (~$8/month for this bot)

> **Tip**: Set a budget alert! Go to portal → Subscriptions → Cost Management → Budgets → Add → Set $15/month → Get email alert at 80%.

---

## PART 2: Install Azure CLI on Your Windows PC

### Step 2.1 — Download & Install

1. Open this URL in your browser: **https://aka.ms/installazurecli**
2. Download the MSI installer (64-bit)
3. Run the installer → Next → Next → Install → Finish
4. **Close and reopen** any PowerShell/terminal windows

### Step 2.2 — Verify installation

Open a **new PowerShell window** and run:
```powershell
az --version
```
You should see something like `azure-cli 2.x.x`.

### Step 2.3 — Login to Azure

```powershell
az login
```
This opens your browser. Sign in with the same Microsoft account you used for Azure. After login, you'll see your subscription details in the terminal.

Verify you're logged in:
```powershell
az account show
```

---

## PART 3: Set Up Your Environment Files

Your bot needs 4 secrets. You'll have TWO versions — one for demo testing, one for live trading.

### Step 3.1 — Understand the secrets

| Secret | Where to get it |
|--------|----------------|
| `BINANCE_API_KEY` | Binance → Profile → API Management → Create API |
| `BINANCE_API_SECRET` | Same page (shown once when you create the key) |
| `TELEGRAM_BOT_TOKEN` | Telegram → message @BotFather → /newbot → copy token |
| `TELEGRAM_CHAT_ID` | Telegram → message @userinfobot → copy your chat ID |

### Step 3.2 — Create your DEMO .env file (for testing)

In your project root (`C:\Users\anjan\source\repos\2903FinalStrategy`), your `.env` file should look like this for **demo/testnet** trading:

```
BINANCE_API_KEY=your_demo_api_key_here
BINANCE_API_SECRET=your_demo_api_secret_here
TELEGRAM_BOT_TOKEN=your_telegram_bot_token
TELEGRAM_CHAT_ID=your_telegram_chat_id
```

> **Demo API keys** come from https://testnet.binancefuture.com (for testnet) or the Binance demo trading page.

### Step 3.3 — Create your LIVE .env file (for real money)

When you're ready to go live, replace the demo keys with your **real** Binance API keys:

```
BINANCE_API_KEY=your_REAL_api_key
BINANCE_API_SECRET=your_REAL_api_secret
TELEGRAM_BOT_TOKEN=your_telegram_bot_token
TELEGRAM_CHAT_ID=your_telegram_chat_id
```

> **IMPORTANT**: For live trading, you also need `testnet: false` in `config/config.yaml` OR the deploy script sets `TESTNET=false` automatically as an environment variable.

### Step 3.4 — Binance API Key security settings

When creating your **LIVE** API key on Binance:
1. Go to https://www.binance.com → Profile → API Management
2. Create new API key → **"System generated"**
3. Enable: **"Enable Futures"** only
4. **Restrict to IP**: After deployment, get your VM's IP and add it here
5. **DO NOT** enable withdrawals
6. **DO NOT** enable spot trading (you only need futures)

---

## PART 4: Config File — Demo vs Live

### Step 4.1 — For DEMO deployment

In `config/config.yaml`, make sure:
```yaml
exchange:
  testnet: true      # <-- true = demo mode
```

### Step 4.2 — For LIVE deployment

Change to:
```yaml
exchange:
  testnet: false     # <-- false = real money
```

Or leave it as `true` in the file — the deploy script passes `TESTNET=false` as an environment variable which overrides the config file.

---

## PART 5: Deploy to Azure (Step by Step)

### Step 5.1 — Open PowerShell

1. Open **Windows Terminal** or **PowerShell**
2. Navigate to your project:
```powershell
cd C:\Users\anjan\source\repos\2903FinalStrategy
```

### Step 5.2 — Verify your .env file has the right keys

```powershell
Get-Content .env
```
Make sure all 4 values are filled in (not empty).

### Step 5.3 — Run the deployment

```powershell
.\deploy\deploy-azure.ps1
```

**What happens behind the scenes:**
1. Checks Azure CLI is installed and you're logged in
2. Reads your `.env` file to get the 4 secrets
3. Packages your bot code into a compressed file
4. Creates an Azure Resource Group (a folder for your resources)
5. Creates a B1s VM (Ubuntu Linux, 1 CPU, 1 GB RAM)
6. Uploads your code to the VM via SSH
7. Installs Python 3.12, creates virtual environment, installs packages
8. Creates a systemd service that auto-starts and auto-restarts the bot
9. Uploads your `.env` secrets securely to the VM
10. Starts the bot

**Expected output:**
```
>> Pre-flight checks
   Azure CLI: your@email.com (Pay-As-You-Go)

>> Loading secrets from .env
   All secrets loaded

>> Packaging bot code
   Package: 45.2 KB

>> 1/4  Resource Group
   rg-scalping-bot in northeurope

>> 2/4  Creating VM (Standard_B1s)
   VM created: Standard_B1s (1 vCPU, 1 GB RAM, 30 GB SSD)
   Waiting 30s for VM to initialize...

>> 3/4  Uploading to 20.xxx.xxx.xxx
   Files uploaded

>> 4/4  Running setup on VM
   >>> [1/5] System packages
   >>> [2/5] Create bot user & directories
   >>> [3/5] Deploy bot code
   >>> [4/5] Python venv & dependencies
   >>> [5/5] Systemd service
   ● scalping-bot.service - ETH/USDT Scalping Bot
     Active: active (running)

================================================================
  DEPLOYMENT COMPLETE
================================================================
```

> **First deployment takes 3-5 minutes** (VM creation + software install). Updates take ~30 seconds.

### Step 5.4 — Verify the bot is running

Check status:
```powershell
.\deploy\deploy-azure.ps1 -Action status
```

Watch live logs:
```powershell
.\deploy\deploy-azure.ps1 -Action logs
```
Press `Ctrl+C` to stop watching logs.

You should also receive a **Telegram startup notification** from the bot.

---

## PART 6: Day-to-Day Management

### See live logs
```powershell
.\deploy\deploy-azure.ps1 -Action logs
```

### Check if bot is running
```powershell
.\deploy\deploy-azure.ps1 -Action status
```

### Stop the bot
```powershell
.\deploy\deploy-azure.ps1 -Action stop
```

### Start the bot
```powershell
.\deploy\deploy-azure.ps1 -Action start
```

### Restart the bot
```powershell
.\deploy\deploy-azure.ps1 -Action restart
```

### SSH into the VM (advanced)
```powershell
.\deploy\deploy-azure.ps1 -Action ssh
```
Once connected, useful commands on the VM:
```bash
# Check bot status
sudo systemctl status scalping-bot

# View last 100 lines of bot log
tail -100 /opt/scalping-bot/logs/bot.log

# View the trades database
sqlite3 /opt/scalping-bot/data/trades.db "SELECT * FROM trades ORDER BY id DESC LIMIT 10;"

# View position state
cat /opt/scalping-bot/data/live_state.json

# Exit SSH
exit
```

### Push code changes (after editing code locally)
```powershell
.\deploy\deploy-azure.ps1 -Action update
```
This re-uploads your code and restarts the bot. Takes ~30 seconds.

---

## PART 7: Switch from Demo to Live

### Step 7.1 — Stop the bot
```powershell
.\deploy\deploy-azure.ps1 -Action stop
```

### Step 7.2 — Update your local `.env` with REAL Binance API keys
Edit `C:\Users\anjan\source\repos\2903FinalStrategy\.env`:
```
BINANCE_API_KEY=your_REAL_key
BINANCE_API_SECRET=your_REAL_secret
TELEGRAM_BOT_TOKEN=same_token
TELEGRAM_CHAT_ID=same_chat_id
```

### Step 7.3 — Set testnet to false
Edit `config/config.yaml`:
```yaml
exchange:
  testnet: false
```

### Step 7.4 — Push the update
```powershell
.\deploy\deploy-azure.ps1 -Action update
```

### Step 7.5 — Lock down Binance API key
1. Get your VM's IP:
   ```powershell
   .\deploy\deploy-azure.ps1 -Action status
   ```
   Note the IP address shown.
2. Go to Binance → API Management → Edit your key → **"Restrict access to trusted IPs only"** → Add the VM IP
3. This means even if your API key leaks, it only works from your VM

---

## PART 8: Delete Everything (Teardown)

If you want to stop paying and delete all Azure resources:

```powershell
.\deploy\deploy-azure.ps1 -Action teardown
```

Type `yes` to confirm. This deletes:
- The VM
- The disk
- The network resources
- The resource group

**Your local code is NOT affected.** You can redeploy anytime with `.\deploy\deploy-azure.ps1`.

---

## PART 9: Troubleshooting

### "Azure CLI not found"
→ Install from https://aka.ms/installazurecli, then **close and reopen** PowerShell.

### "Not logged in"
→ Run `az login` and sign in via browser.

### Deployment fails at "Creating VM"
→ Your subscription might not be set up for the region. Try:
```powershell
.\deploy\deploy-azure.ps1 -Location eastus
```

### Bot crashes repeatedly
→ Check logs:
```powershell
.\deploy\deploy-azure.ps1 -Action logs
```

### "Permission denied" on SSH
→ Your SSH key might not match. SSH into Azure portal → VM → Reset password → Reset SSH public key.

### Bot not placing trades
→ Check:
1. Is `testnet` set correctly? (true for demo, false for live)
2. Are API keys correct and have futures permission?
3. Is there enough balance in your Binance futures account?
4. Check logs for "Trade rejected" messages

---

## Quick Reference Card

| What | Command |
|------|---------|
| **First deploy** | `.\deploy\deploy-azure.ps1` |
| **Push code changes** | `.\deploy\deploy-azure.ps1 -Action update` |
| **View logs** | `.\deploy\deploy-azure.ps1 -Action logs` |
| **Bot status** | `.\deploy\deploy-azure.ps1 -Action status` |
| **Restart** | `.\deploy\deploy-azure.ps1 -Action restart` |
| **Stop** | `.\deploy\deploy-azure.ps1 -Action stop` |
| **Start** | `.\deploy\deploy-azure.ps1 -Action start` |
| **SSH into VM** | `.\deploy\deploy-azure.ps1 -Action ssh` |
| **Delete everything** | `.\deploy\deploy-azure.ps1 -Action teardown` |
| **Deploy to different region** | `.\deploy\deploy-azure.ps1 -Location southeastasia` |
| **Check logs** | `$ip = az vm show --name vm-scalping-bot --resource-group rg-scalping-bot --show-details --query publicIps -o tsv ssh azureuser@$ip "sudo journalctl -u scalping-bot --no-pager -n 100"`|
| **Monthly cost** | ~$7.59 (VM) + ~$0.40 (disk) = **~$8/month** |
