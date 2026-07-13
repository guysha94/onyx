# Bootstrap Let's Encrypt for the dev:gpu stack (manual DNS-01 default, HTTP-01 fallback).
#
# Requires in .env.nginx:
#   DOMAIN
# Optional:
#   EMAIL, LETSENCRYPT_STAGING, CERTBOT_CHALLENGE=manual-dns|webroot

$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ScriptDir

if (-not (Test-Path ".env.nginx")) {
    Write-Error "Missing .env.nginx in $ScriptDir"
}

$envVars = @{}
Get-Content ".env.nginx" | ForEach-Object {
    $line = $_.Trim()
    if ($line -eq "" -or $line.StartsWith("#")) {
        return
    }
    $parts = $line -split "=", 2
    if ($parts.Count -eq 2) {
        $envVars[$parts[0].Trim()] = $parts[1].Trim()
    }
}

$DOMAIN = $envVars["DOMAIN"]
$EMAIL = $envVars["EMAIL"]
$CHALLENGE = if ($envVars.ContainsKey("CERTBOT_CHALLENGE")) { $envVars["CERTBOT_CHALLENGE"] } else { "manual-dns" }
$LETSENCRYPT_STAGING = if ($envVars.ContainsKey("LETSENCRYPT_STAGING")) { $envVars["LETSENCRYPT_STAGING"] } else { "0" }

if ([string]::IsNullOrWhiteSpace($DOMAIN)) {
    Write-Error "DOMAIN must be set in .env.nginx"
}

$ComposeArgs = @(
    "-f", "docker-compose.yml",
    "-f", "docker-compose.dev.yml",
    "-f", "docker-compose.gpu.yml",
    "-f", "docker-compose.dev-ssl.yml"
)

function Invoke-Compose {
    param(
        [Parameter(ValueFromRemainingArguments = $true)]
        [string[]]$Args
    )
    & docker compose @ComposeArgs @Args
    if ($LASTEXITCODE -ne 0) {
        throw "docker compose failed (exit $LASTEXITCODE): docker compose $($ComposeArgs -join ' ') $($Args -join ' ')"
    }
}

function Invoke-ComposeCertbot {
    param(
        [string]$Entrypoint,
        [string[]]$ExtraArgs = @(),
        [switch]$Interactive
    )
    $runArgs = @("run", "--name", "onyx", "--rm")
    if ($Interactive) {
        $runArgs += "-it"
    }
    $runArgs += $ExtraArgs
    $runArgs += @("--entrypoint", $Entrypoint, "certbot")
    Invoke-Compose @runArgs
}

function New-DummyCertificate {
    param([string]$Domain, [int]$RsaKeySize)
    $dataPath = Join-Path $ScriptDir "..\data\certbot"
    $liveCertPath = Join-Path $dataPath "conf\live\$Domain"
    $certPath = "/etc/letsencrypt/live/$Domain"
    Write-Host "### Creating temporary self-signed certificate for $Domain ..."
    New-Item -ItemType Directory -Force -Path $liveCertPath | Out-Null
    $dummyEntrypoint = "openssl req -x509 -nodes -newkey rsa:$RsaKeySize -days 1 -keyout '$certPath/privkey.pem' -out '$certPath/fullchain.pem' -subj '/CN=localhost'"
    Invoke-ComposeCertbot -Entrypoint $dummyEntrypoint
}

function Remove-CertificateFiles {
    param([string]$Domain)
    Write-Host "### Removing existing certificate files for $Domain ..."
    $deleteEntrypoint = "rm -Rf /etc/letsencrypt/live/$Domain && rm -Rf /etc/letsencrypt/archive/$Domain && rm -Rf /etc/letsencrypt/renewal/$Domain.conf"
    Invoke-ComposeCertbot -Entrypoint $deleteEntrypoint
}

function Rename-CertificateDirectoryIfNeeded {
    param([string]$Domain)
    $fullchain = Join-Path $ScriptDir "..\data\certbot\conf\live\$Domain\fullchain.pem"
    if (Test-Path $fullchain) {
        Write-Host "### Certificate already at live/$Domain (no rename needed)"
        return
    }
    Write-Host "### Renaming certificate directory if needed ..."
    $renameEntrypoint = @"
find /etc/letsencrypt/live -maxdepth 1 -type d -name '$Domain-00*' | sort -r | head -n1 | xargs -r -I{} mv {} /etc/letsencrypt/live/$Domain
"@
    Invoke-ComposeCertbot -Entrypoint $renameEntrypoint
}

docker compose version *> $null
if ($LASTEXITCODE -ne 0) {
    Write-Error "docker compose is not installed or not available in PATH"
}

$rsaKeySize = 4096
$dataPath = Join-Path $ScriptDir "..\data\certbot"
$liveCertPath = Join-Path $dataPath "conf\live\$DOMAIN"
$fullchainPath = Join-Path $liveCertPath "fullchain.pem"
$force = ($env:FORCE_CERT_RENEW -eq "1") -or ($args -contains "-Force")

if ((Test-Path $fullchainPath) -and -not $force) {
    $decision = Read-Host "Existing certificate found for $DOMAIN. Replace it? (y/N)"
    if ($decision -notin @("Y", "y")) {
        exit 0
    }
}

$optionsSsl = Join-Path $dataPath "conf\options-ssl-nginx.conf"
$dhParams = Join-Path $dataPath "conf\ssl-dhparams.pem"
if (-not (Test-Path $optionsSsl) -or -not (Test-Path $dhParams)) {
    Write-Host "### Downloading recommended TLS parameters ..."
    New-Item -ItemType Directory -Force -Path (Join-Path $dataPath "conf") | Out-Null
    Invoke-WebRequest -UseBasicParsing -Uri "https://raw.githubusercontent.com/certbot/certbot/master/certbot-nginx/certbot_nginx/_internal/tls_configs/options-ssl-nginx.conf" -OutFile $optionsSsl
    Invoke-WebRequest -UseBasicParsing -Uri "https://raw.githubusercontent.com/certbot/certbot/master/certbot/certbot/ssl-dhparams.pem" -OutFile $dhParams
}

if ([string]::IsNullOrWhiteSpace($EMAIL)) {
    $emailArg = "--register-unsafely-without-email"
} else {
    $emailArg = "--email $EMAIL"
}

$stagingArg = ""
if ($LETSENCRYPT_STAGING -ne "0") {
    $stagingArg = "--staging"
}

$certIssued = $false
$certbotBaseArgs = "$stagingArg $emailArg -d $DOMAIN --rsa-key-size $rsaKeySize --agree-tos --force-renewal"
$certbotNonInteractiveArgs = "$certbotBaseArgs --non-interactive"

if ($CHALLENGE -eq "manual-dns") {
    if ($force -or (Test-Path $fullchainPath)) {
        Remove-CertificateFiles -Domain $DOMAIN
    }

    Write-Host ""
    Write-Host "### Manual DNS-01 for $DOMAIN"
    Write-Host "Certbot will print a TXT record like:"
    Write-Host "  _acme-challenge.$DOMAIN  TXT  <value>"
    Write-Host ""
    Write-Host "1. Create that TXT record in Route 53 (superplay.dev zone)"
    Write-Host "2. Wait for propagation: nslookup -type=TXT _acme-challenge.$DOMAIN"
    Write-Host "3. Press Enter in the certbot prompt when ready"
    Write-Host ""

    try {
        $manualEntrypoint = "certbot certonly --manual --preferred-challenges dns $certbotBaseArgs"
        Invoke-ComposeCertbot -Entrypoint $manualEntrypoint -Interactive
        $certIssued = $true
    } catch {
        Write-Host "### Manual DNS-01 failed: $($_.Exception.Message)"
    }
} else {
    if ($force -or -not (Test-Path $fullchainPath)) {
        Remove-CertificateFiles -Domain $DOMAIN
    }
    New-DummyCertificate -Domain $DOMAIN -RsaKeySize $rsaKeySize

    Write-Host "### Starting nginx ..."
    Invoke-Compose up --force-recreate -d --wait --wait-timeout 300 nginx

    Remove-CertificateFiles -Domain $DOMAIN

    Write-Host "### Requesting certificate via HTTP-01 (webroot) ..."
    $webrootSucceeded = $false
    try {
        $webrootEntrypoint = "certbot certonly --webroot -w /var/www/certbot $certbotNonInteractiveArgs"
        Invoke-ComposeCertbot -Entrypoint $webrootEntrypoint
        $webrootSucceeded = $true
    } catch {
        Write-Host "### HTTP-01 failed: $($_.Exception.Message)"
    }

    if (-not $webrootSucceeded) {
        Write-Host "### Trying standalone HTTP-01 on port 80 ..."
        Invoke-Compose stop nginx
        try {
            $standaloneEntrypoint = "certbot certonly --standalone --preferred-challenges http-01 --http-01-port 80 $certbotNonInteractiveArgs"
            Invoke-ComposeCertbot -Entrypoint $standaloneEntrypoint -ExtraArgs @("-p", "80:80", "-p", "443:443")
            $webrootSucceeded = $true
        } catch {
            Write-Host "### Standalone issuance also failed: $($_.Exception.Message)"
            New-DummyCertificate -Domain $DOMAIN -RsaKeySize $rsaKeySize
        } finally {
            Invoke-Compose up -d nginx
        }
    }

    $certIssued = $webrootSucceeded
}

if (-not $certIssued) {
    Write-Warning @"
Let's Encrypt issuance failed.
Re-run in an interactive terminal: mise run dev:gpu:init-certs
"@
    exit 1
}

Rename-CertificateDirectoryIfNeeded -Domain $DOMAIN

if ($CHALLENGE -eq "manual-dns") {
    Write-Host "### Starting nginx (manual DNS: auto-renew disabled) ..."
    Invoke-Compose up --force-recreate -d --wait --wait-timeout 300 nginx
} else {
    Write-Host "### Starting nginx + certbot renewal sidecar ..."
    Invoke-Compose up --force-recreate -d --wait --wait-timeout 300 nginx certbot
}

Write-Host "### Done. Access Onyx at https://$DOMAIN"
