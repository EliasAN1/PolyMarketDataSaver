# Proxy http://127.0.0.1:8080 (pmdsaver dashboard) onto your tailnet (HTTPS).
# Other PC / phone: Tailscale app, same account, then open the URL printed below.
# Keep the dashboard on 127.0.0.1 — do not bind 0.0.0.0.

$ErrorActionPreference = "Stop"
tailscale up
tailscale serve --bg 8080
tailscale serve status
$dns = (tailscale status --json | ConvertFrom-Json).Self.DNSName.TrimEnd(".")
Write-Host ""
Write-Host "On the other computer (Tailscale must be connected):"
Write-Host "  https://$dns/"
Write-Host "  https://$dns/backtest"
Write-Host ""
Write-Host "To stop sharing: tailscale serve reset"
