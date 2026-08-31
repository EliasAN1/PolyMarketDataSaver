# Proxy http://127.0.0.1:3848 onto your tailnet (HTTPS).
# Phone: Tailscale app, same account, then open the URL printed below.
# Keep the trader UI on 127.0.0.1 — do not bind 0.0.0.0.

$ErrorActionPreference = "Stop"
tailscale up
tailscale serve --bg 3848
tailscale serve status
$dns = (tailscale status --json | ConvertFrom-Json).Self.DNSName.TrimEnd(".")
Write-Host ""
Write-Host "On the phone (Tailscale must be connected):"
Write-Host "  https://$dns/"
