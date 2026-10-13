# Installs Cameo on Windows 10/11: a Python environment (.venv), the packages, the voice model and a
# desktop shortcut. Needs Python 3.10-3.12 (the "py" launcher from python.org, or python on PATH).
$ErrorActionPreference = 'Stop'
$root = $PSScriptRoot
$venv = Join-Path $root '.venv'
$python = Join-Path $venv 'Scripts\python.exe'

if (-not (Test-Path -LiteralPath $python)) {
    $base = $null
    foreach ($version in '3.12', '3.11', '3.10') {
        if (Get-Command py -ErrorAction SilentlyContinue) {
            & py "-$version" -c "import sys" 2>$null
            if ($LASTEXITCODE -eq 0) { $base = @('py', "-$version"); break }
        }
    }
    if (-not $base -and (Get-Command python -ErrorAction SilentlyContinue)) { $base = @('python') }
    if (-not $base) { throw 'Python 3.10-3.12 is required: install it from python.org, then run install.ps1 again.' }
    Write-Host "Creating $venv ..."
    $version = @($base | Select-Object -Skip 1)
    & $base[0] @version -m venv $venv
    if ($LASTEXITCODE -ne 0) { throw 'Could not create the Python environment' }
}
& $python -m pip install --upgrade pip
& $python -m pip install -r (Join-Path $root 'requirements.txt')
if ($LASTEXITCODE -ne 0) { throw 'Package installation failed' }
& $python (Join-Path $root 'scripts\setup_models.py')
if ($LASTEXITCODE -ne 0) { throw 'Voice model download failed' }

$shortcut = (New-Object -ComObject WScript.Shell).CreateShortcut((Join-Path ([Environment]::GetFolderPath('Desktop')) 'Cameo.lnk'))
$shortcut.TargetPath = Join-Path $root 'Start Cameo.bat'
$shortcut.WorkingDirectory = $root
$shortcut.WindowStyle = 7  # minimized: the console closes at once anyway
$shortcut.Save()
# Grab and drop between devices: firewall rules and the .cameo file type (asks for administrator rights).
& (Join-Path $root 'scripts\setup_link.ps1')
& $python -m cameo doctor
Write-Host 'Cameo is installed. Start it with the Cameo shortcut on the desktop.'
