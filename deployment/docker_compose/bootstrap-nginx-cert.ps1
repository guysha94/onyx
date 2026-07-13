# Restore a temporary cert so nginx can start when real LE certs are missing.
$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ScriptDir

$domain = "sp-ai-platform.superplay.dev"
if (Test-Path ".env.nginx") {
    $line = Get-Content ".env.nginx" | Where-Object { $_ -match '^DOMAIN=' } | Select-Object -First 1
    if ($line) { $domain = ($line -split '=', 2)[1].Trim() }
}

$compose = @("-f","docker-compose.yml","-f","docker-compose.dev.yml","-f","docker-compose.gpu.yml","-f","docker-compose.dev-ssl.yml")
$certPath = "/etc/letsencrypt/live/$domain"
New-Item -ItemType Directory -Force -Path "..\data\certbot\conf\live\$domain" | Out-Null
$dummy = "openssl req -x509 -nodes -newkey rsa:4096 -days 90 -keyout '$certPath/privkey.pem' -out '$certPath/fullchain.pem' -subj '/CN=$domain'"
docker compose @compose run --name onyx --rm --entrypoint $dummy certbot
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
docker compose @compose up -d --force-recreate nginx
Write-Host "nginx bootstrapped with temporary self-signed cert for $domain"
