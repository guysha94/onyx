# Quick check that public port 80 reaches nginx (only required for HTTP-01 / CERTBOT_CHALLENGE=webroot).
$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$pingPath = Join-Path $ScriptDir "..\data\certbot\www\.well-known\acme-challenge\ping"
New-Item -ItemType Directory -Force -Path (Split-Path $pingPath) | Out-Null
Set-Content -Path $pingPath -Value "ok" -NoNewline

$domain = "sp-ai-platform.superplay.dev"
if (Test-Path (Join-Path $ScriptDir ".env.nginx")) {
    $line = Get-Content (Join-Path $ScriptDir ".env.nginx") | Where-Object { $_ -match '^DOMAIN=' } | Select-Object -First 1
    if ($line) { $domain = ($line -split '=', 2)[1].Trim() }
}

$publicIp = (Invoke-RestMethod -Uri "https://api.ipify.org?format=json" -TimeoutSec 10).ip
Write-Host "Host public IP:  $publicIp"
Write-Host "LAN IP:          192.168.101.161 (expected forward target)"
Write-Host "Domain:          $domain"
Write-Host ""

function Test-Url([string]$Label, [string]$Url) {
    try {
        $r = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 15
        $ok = ($r.StatusCode -eq 200 -and $r.Content.Trim() -eq "ok")
        $status = if ($ok) { "PASS" } else { "FAIL (status $($r.StatusCode), body '$($r.Content)')" }
        Write-Host "[$status] $Label -> $Url"
        return $ok
    } catch {
        $code = "connection failed"
        if ($null -ne $_.Exception.Response -and $null -ne $_.Exception.Response.StatusCode) {
            $code = "HTTP $($_.Exception.Response.StatusCode.value__)"
        }
        Write-Host "[FAIL] $Label -> $Url ($code)"
        return $false
    }
}

$localOk = Test-Url "local nginx" "http://127.0.0.1/.well-known/acme-challenge/ping"
$lanOk = Test-Url "LAN nginx" "http://192.168.101.161/.well-known/acme-challenge/ping"
$publicOk = Test-Url "public IP" "http://$publicIp/.well-known/acme-challenge/ping"
$domainOk = Test-Url "public domain" "http://$domain/.well-known/acme-challenge/ping"

Write-Host ""
if ($localOk -and $lanOk -and $publicOk -and $domainOk) {
    Write-Host "All checks passed. Run: mise run dev:gpu:init-certs"
    exit 0
}

Write-Host "Port forwarding is NOT working end-to-end."
Write-Host "On router 192.168.100.99, add NAT rules:"
Write-Host "  WAN TCP 80  -> 192.168.101.161:80"
Write-Host "  WAN TCP 443 -> 192.168.101.161:443"
Write-Host "Then re-run: mise run dev:gpu:check-ports"
exit 1
