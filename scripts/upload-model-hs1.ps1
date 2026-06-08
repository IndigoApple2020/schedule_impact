$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

function Say($msg) { Write-Host $msg; [Console]::Out.Flush() }

$REMOTE_USER = "alexc"
$REMOTE_HOST = if ($env:REMOTE_HOST) { $env:REMOTE_HOST } else { "hs1.headwayrail.co.uk" }
$REMOTE = "${REMOTE_USER}@${REMOTE_HOST}"

$REMOTE_HOST_WSL = if ($env:REMOTE_HOST_WSL) { $env:REMOTE_HOST_WSL } else { $REMOTE_HOST }
$REMOTE_DIR = "/home/alexc/models"

$LOCAL_FILE_WIN = "C:\Users\admin\Downloads\mistral-7b-instruct-v0.2.Q4_K_M.gguf"
$LOCAL_FILE_NAME = Split-Path $LOCAL_FILE_WIN -Leaf

$RETRY_SECONDS = 15
$RETRY_SECONDS_AFTER_DROP = 30
$BW_LIMIT_KBPS = if ($env:BW_LIMIT_KBPS -match '^\d+$') { [int]$env:BW_LIMIT_KBPS } else { 0 }

$SSH_OPTS = @(
    "-o", "ServerAliveInterval=15",
    "-o", "ServerAliveCountMax=4",
    "-o", "TCPKeepAlive=yes",
    "-o", "ConnectTimeout=60"
)

$UseWslRsync = $true

$drive = $LOCAL_FILE_WIN.Substring(0,1).ToLower()
$rest = $LOCAL_FILE_WIN.Substring(2) -replace '\\','/'
$LOCAL_FILE_WSL = "/mnt/$drive$rest"

Say "==> Upload model to hs1 starting..."
Say "==> Local:  $LOCAL_FILE_WIN"
Say "==> Remote: ${REMOTE}:${REMOTE_DIR}/$LOCAL_FILE_NAME"

function Need-Cmd($name) {
    $c = Get-Command $name -ErrorAction SilentlyContinue
    if (-not $c) { Write-Error "Missing command: $name" }
}

Say "==> Checking required commands..."
Need-Cmd "ssh"
Need-Cmd "wsl"

$null = wsl -e which rsync 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Error "rsync not found in WSL. Install with: wsl -e sudo apt install -y rsync"
}

Say "==> Ensure remote dir exists"
$sshAttempts = 0
$sshMaxAttempts = 3
while ($true) {
    & ssh @SSH_OPTS $REMOTE "mkdir -p '$REMOTE_DIR'"
    if ($LASTEXITCODE -eq 0) { break }

    $sshAttempts++
    if ($sshAttempts -ge $sshMaxAttempts) {
        throw "ssh mkdir failed after $sshMaxAttempts attempts"
    }

    Say "    SSH failed (attempt $sshAttempts/$sshMaxAttempts). Retrying in $RETRY_SECONDS s..."
    Start-Sleep -Seconds $RETRY_SECONDS
}

function Upload-One($localPathWsl) {
    $sshOptsStr = $SSH_OPTS -join " "

    while ($true) {
        $bw = if ($BW_LIMIT_KBPS -gt 0) { "--bwlimit=$BW_LIMIT_KBPS" } else { "" }
        $remoteDest = "${REMOTE_USER}@${REMOTE_HOST_WSL}:${REMOTE_DIR}/"

        $cmd = "rsync -avP --partial --append-verify --timeout=120 -e 'ssh $sshOptsStr' $bw '$localPathWsl' '$remoteDest'"
        & wsl bash -c "$cmd"

        $code = $LASTEXITCODE

        if ($code -eq 0) {
            Say "    Upload complete: $LOCAL_FILE_NAME"
            return
        }

        $wait = $RETRY_SECONDS
        if ($code -eq 255) { $wait = $RETRY_SECONDS_AFTER_DROP }

        if ($code -eq 255) {
            Say "    Upload failed (exit 255). If hostname does not resolve in WSL, set:"
            Say "    `$env:REMOTE_HOST_WSL = `"<hs1-IP-or-FQDN>`""
        }

        Say "    Upload interrupted (exit $code). Retrying in ${wait}s..."
        Start-Sleep -Seconds $wait
    }
}

Say "==> Uploading with WSL rsync"
Upload-One $LOCAL_FILE_WSL

Say "==> Verify remote file"
& ssh @SSH_OPTS $REMOTE "ls -lh '$REMOTE_DIR/$LOCAL_FILE_NAME'"

Say "==> Done."