# Grab and drop between devices, on Windows: opens Cameo's link ports in Windows Firewall (Private networks only)
# and makes .cameo package files open with Cameo. Run once on each Windows device; it asks for administrator rights.
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$principal = [Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Start-Process powershell -Verb RunAs -Wait -ArgumentList "-NoProfile -ExecutionPolicy Bypass -File `"$PSCommandPath`""
    exit
}

# Links (TCP 47820), discovery beacons (UDP 47821) and mDNS (UDP 5353). Never on Public networks.
$rules = @(
    @{ Name = 'Cameo Link TCP'; Protocol = 'TCP'; Port = 47820 },
    @{ Name = 'Cameo Link UDP'; Protocol = 'UDP'; Port = 47821 },
    @{ Name = 'Cameo Link mDNS'; Protocol = 'UDP'; Port = 5353 }
)
foreach ($rule in $rules) {
    Get-NetFirewallRule -DisplayName $rule.Name -ErrorAction SilentlyContinue | Remove-NetFirewallRule
    New-NetFirewallRule -DisplayName $rule.Name -Direction Inbound -Action Allow -Protocol $rule.Protocol `
        -LocalPort $rule.Port -Profile Private, Domain | Out-Null
    Write-Host "Firewall: $($rule.Name) open on Private networks"
}
$network = (Get-NetConnectionProfile | Select-Object -First 1)
if ($network -and $network.NetworkCategory -ne 'Private') {
    Write-Host "Your network '$($network.Name)' is $($network.NetworkCategory): Cameo devices cannot find each other on it."
    Write-Host 'Set it to Private in Settings > Network & internet > (your network) > Network profile type.'
}

# .cameo files open with Cameo: unpacked into the inbox and shown.
$pythonw = Join-Path $root 'runtime\controller-env\Scripts\pythonw.exe'
if (-not (Test-Path -LiteralPath $pythonw)) { $pythonw = Join-Path $root '.venv\Scripts\pythonw.exe' }
$opener = Join-Path $root 'scripts\open_package.pyw'
$classes = 'HKCU:\Software\Classes'
New-Item -Path "$classes\.cameo" -Force -Value 'Cameo.Package' | Out-Null
New-Item -Path "$classes\Cameo.Package" -Force -Value 'Cameo package' | Out-Null
New-Item -Path "$classes\Cameo.Package\shell\open\command" -Force -Value "`"$pythonw`" `"$opener`" `"%1`"" | Out-Null
Write-Host '.cameo files now open with Cameo'
Start-Sleep -Seconds 3
