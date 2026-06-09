<#
.SYNOPSIS
    Deploy the ETH/USDT scalping bot to an Azure B1s Virtual Machine.

.DESCRIPTION
    Creates all Azure resources needed:
      1. Resource Group
      2. Azure B1s VM (1 vCPU, 1 GB RAM, Ubuntu 24.04) — ~$7.59/month
      3. Uploads bot code, installs Python, configures systemd auto-restart
      4. Secrets passed via .env file (never stored in Azure metadata)

    Prerequisites:
      - Azure CLI installed and logged in: az login
      - Local .env file with BINANCE_API_KEY, BINANCE_API_SECRET, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
      - SSH key pair (~/.ssh/id_rsa.pub) — will be created if missing

.PARAMETER Location
    Azure region. Default: northeurope (low latency to Binance EU endpoints).

.PARAMETER VmSize
    VM SKU. Default: Standard_B1s (~$7.59/month).

.PARAMETER Action
    deploy  — Full deployment (create VM + upload code + start bot)
    update  — Re-upload code & restart bot on existing VM
    ssh     — Open SSH session to the VM
    logs    — Tail live bot logs
    status  — Show bot service status
    start   — Start the bot
    stop    — Stop the bot
    restart — Restart the bot
    teardown — Delete all Azure resources

.EXAMPLE
    .\deploy-azure.ps1                          # First deployment
    .\deploy-azure.ps1 -Action update           # Push code changes
    .\deploy-azure.ps1 -Action logs             # Tail logs
    .\deploy-azure.ps1 -Action ssh              # SSH into VM
    .\deploy-azure.ps1 -Action teardown         # Delete everything
#>

param(
    [string]$Location = "uksouth",
    [string]$VmSize = "Standard_B1s",
    [ValidateSet("deploy", "update", "ssh", "logs", "status", "start", "stop", "restart", "teardown")]
    [string]$Action = "deploy"
)

$ErrorActionPreference = "Stop"

# ── Resource names ──────────────────────────────────────────────────────────
$RG      = "rg-scalping-bot"
$VM_NAME = "vm-scalping-bot"
$NSG     = "nsg-scalping-bot"
$VM_USER = "azureuser"

# ── Helpers ─────────────────────────────────────────────────────────────────
function Write-Step($msg) { Write-Host "`n>> $msg" -ForegroundColor Cyan }
function Write-OK($msg)   { Write-Host "   $msg" -ForegroundColor Green }
function Write-Warn($msg) { Write-Host "   $msg" -ForegroundColor Yellow }

function Get-VmIp {
    $result = $null
    try { $result = az vm show --name $VM_NAME --resource-group $RG --show-details --query publicIps -o tsv 2>&1 | Out-String } catch {}
    $ip = if ($result -and $result -notmatch 'ERROR') { $result.Trim() } else { $null }
    if (-not $ip) { Write-Error "VM not found or has no public IP. Run deploy first." }
    return $ip
}

function Invoke-Ssh {
    param([string]$Command)
    $ip = Get-VmIp
    ssh -o StrictHostKeyChecking=no -o ConnectTimeout=10 "$VM_USER@$ip" $Command
}

# ── Pre-flight ──────────────────────────────────────────────────────────────
Write-Step "Pre-flight checks"

if (-not (Get-Command az -ErrorAction SilentlyContinue)) {
    Write-Error "Azure CLI not found. Install from https://aka.ms/installazurecli"
}
$azAccount = az account show 2>$null | ConvertFrom-Json
if (-not $azAccount) { Write-Error "Not logged in. Run: az login" }
Write-OK "Azure CLI: $($azAccount.user.name) ($($azAccount.name))"

# ── Quick actions (no full deploy needed) ───────────────────────────────────
switch ($Action) {
    "ssh"     { $ip = Get-VmIp; Write-OK "Connecting to $ip..."; ssh "$VM_USER@$ip"; exit 0 }
    "logs"    { Invoke-Ssh "sudo journalctl -u scalping-bot -f --no-pager"; exit 0 }
    "status"  { Invoke-Ssh "sudo systemctl status scalping-bot --no-pager -l"; exit 0 }
    "start"   { Invoke-Ssh "sudo systemctl start scalping-bot"; Write-OK "Bot started"; exit 0 }
    "stop"    { Invoke-Ssh "sudo systemctl stop scalping-bot"; Write-OK "Bot stopped"; exit 0 }
    "restart" { Invoke-Ssh "sudo systemctl restart scalping-bot"; Write-OK "Bot restarted"; exit 0 }
    "teardown" {
        Write-Step "Tearing down all resources in $RG"
        $confirm = Read-Host "Delete EVERYTHING in '$RG'? Type 'yes' to confirm"
        if ($confirm -ne "yes") { Write-Host "Aborted." -ForegroundColor Red; exit 0 }
        az group delete --name $RG --yes --no-wait
        Write-OK "Resource group deletion initiated."
        exit 0
    }
}

# ── Load secrets from .env ──────────────────────────────────────────────────
Write-Step "Loading secrets from .env"
$envFile = Join-Path $PSScriptRoot "..\.env"
if (-not (Test-Path $envFile)) {
    Write-Error ".env not found at $envFile"
}

$envVars = @{}
Get-Content $envFile | ForEach-Object {
    if ($_ -match '^\s*([^#][^=]+)=(.*)$') {
        $envVars[$Matches[1].Trim()] = $Matches[2].Trim().Trim('"').Trim("'")
    }
}
foreach ($v in @("BINANCE_API_KEY", "BINANCE_API_SECRET", "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID")) {
    if (-not $envVars[$v]) { Write-Error "Missing $v in .env" }
}
Write-OK "All secrets loaded"

# ── Package bot code ────────────────────────────────────────────────────────
Write-Step "Packaging bot code"
$projectRoot = Join-Path $PSScriptRoot ".."
$tarFile = Join-Path $env:TEMP "bot-code.tar.gz"

Push-Location $projectRoot
tar -czf $tarFile --exclude='.git' --exclude='.venv' --exclude='__pycache__' --exclude='data' --exclude='logs' --exclude='reports' --exclude='.env' --exclude='deploy' --exclude='*.md' --exclude='.dockerignore' --exclude='Dockerfile' .
Pop-Location
$sizeKB = [math]::Round((Get-Item $tarFile).Length / 1KB, 1)
Write-OK "Package: $sizeKB KB"

# ── Deploy: Create VM ──────────────────────────────────────────────────────
if ($Action -eq "deploy") {
    # Ensure SSH key exists
    $sshDir = Join-Path $env:USERPROFILE ".ssh"
    $sshKeyPath = Join-Path $sshDir "id_rsa.pub"
    if (-not (Test-Path $sshKeyPath)) {
        Write-Step "Generating SSH key pair"
        if (-not (Test-Path $sshDir)) { New-Item -ItemType Directory -Path $sshDir -Force | Out-Null }
        $sshPrivKey = Join-Path $sshDir "id_rsa"
        ssh-keygen -t rsa -b 4096 -f $sshPrivKey -N '""' -q
        Write-OK "SSH key created"
    }

    Write-Step "1/4  Resource Group"
    az group create --name $RG --location $Location --output none
    Write-OK "$RG in $Location"

    Write-Step "2/4  Creating VM [$VmSize]"
    $vmExists = $null
    try { $vmExists = az vm show --name $VM_NAME --resource-group $RG 2>&1 | Out-String } catch {}
    if ($vmExists -and $vmExists -notmatch 'ERROR') {
        Write-Warn "VM already exists - will update code only"
    } else {
        $ErrorActionPreference = "Continue"
        $vmResult = az vm create `
            --resource-group $RG `
            --name $VM_NAME `
            --image Canonical:ubuntu-24_04-lts:server:latest `
            --size $VmSize `
            --admin-username $VM_USER `
            --ssh-key-values $sshKeyPath `
            --public-ip-sku Basic `
            --nsg $NSG `
            --storage-sku StandardSSD_LRS `
            --os-disk-size-gb 30 `
            --output json 2>&1 | Out-String
        $ErrorActionPreference = "Stop"

        if ($LASTEXITCODE -ne 0 -or $vmResult -match 'ERROR|SkuNotAvailable') {
            Write-Host ""
            Write-Host "   VM creation FAILED. Azure error:" -ForegroundColor Red
            Write-Host $vmResult -ForegroundColor Red
            Write-Host ""
            Write-Host "   Common fixes:" -ForegroundColor Yellow
            Write-Host "     1. Try a different region:  .\deploy\deploy-azure.ps1 -Location westeurope" -ForegroundColor Yellow
            Write-Host "     2. Try a different VM size: .\deploy\deploy-azure.ps1 -VmSize Standard_B2ats_v2" -ForegroundColor Yellow
            Write-Host "     3. Clean up failed deploy:  .\deploy\deploy-azure.ps1 -Action teardown" -ForegroundColor Yellow
            Write-Host ""
            Write-Error "VM creation failed in $Location. See error above."
        }

        Write-OK "VM created: $VmSize [1 vCPU, 1 GB RAM, 30 GB SSD]"

        # Wait for VM to be ready
        Write-Warn "Waiting 30s for VM to initialize..."
        Start-Sleep -Seconds 30
    }
}

# ── Upload code + secrets + run setup ──────────────────────────────────────
$ip = Get-VmIp
Write-Step "3/4  Uploading to $ip"

scp -o StrictHostKeyChecking=no $tarFile "${VM_USER}@${ip}:/tmp/bot-code.tar.gz"
scp -o StrictHostKeyChecking=no $envFile "${VM_USER}@${ip}:/tmp/bot-env"

$setupScript = Join-Path $PSScriptRoot "vm-setup.sh"
scp -o StrictHostKeyChecking=no $setupScript "${VM_USER}@${ip}:/tmp/vm-setup.sh"

Write-OK "Files uploaded"

Write-Step "4/4  Running setup on VM"
$remoteCmd = 'sudo bash /tmp/vm-setup.sh && sudo mv /tmp/bot-env /opt/scalping-bot/.env && sudo chmod 600 /opt/scalping-bot/.env && sudo chown botuser:botuser /opt/scalping-bot/.env && sudo systemctl restart scalping-bot && sleep 2 && sudo systemctl status scalping-bot --no-pager -l'
ssh -o StrictHostKeyChecking=no "$VM_USER@$ip" $remoteCmd

# Clean up local temp
Remove-Item $tarFile -Force -ErrorAction SilentlyContinue

# ── Summary ─────────────────────────────────────────────────────────────────
Write-Host ""
Write-Host "================================================================" -ForegroundColor Green
Write-Host "  DEPLOYMENT COMPLETE" -ForegroundColor Green
Write-Host "================================================================" -ForegroundColor Green
Write-Host ""
Write-Host "  VM             : $VM_NAME [$VmSize] at $ip"
Write-Host "  OS             : Ubuntu 24.04 LTS"
Write-Host "  Bot path       : /opt/scalping-bot"
Write-Host "  Service        : scalping-bot.service (auto-restart on crash)"
Write-Host "  Log rotation   : 10 MB/file, auto-deleted after 30 days"
Write-Host "  Est. cost      : ~`$7.59/month"
Write-Host ""
Write-Host "  Commands:" -ForegroundColor Yellow
Write-Host "    .\deploy\deploy-azure.ps1 -Action logs      # Tail live logs"
Write-Host "    .\deploy\deploy-azure.ps1 -Action ssh       # SSH into VM"
Write-Host "    .\deploy\deploy-azure.ps1 -Action status    # Service status"
Write-Host "    .\deploy\deploy-azure.ps1 -Action restart   # Restart bot"
Write-Host "    .\deploy\deploy-azure.ps1 -Action stop      # Stop bot"
Write-Host "    .\deploy\deploy-azure.ps1 -Action update    # Push code changes"
Write-Host "    .\deploy\deploy-azure.ps1 -Action teardown  # Delete everything"
Write-Host "================================================================" -ForegroundColor Green
