# Starts NOVA in the background (no console window). Logs: logs\nova.log
#   .\start.ps1            run in the background
#   .\start.ps1 -Console   run in this window with live logs (Ctrl+C to quit)
param([switch]$Console)
Set-Location $PSScriptRoot
if ($Console) {
    & .venv\Scripts\python.exe -m nova
} else {
    Start-Process -FilePath ".venv\Scripts\pythonw.exe" -ArgumentList "-m", "nova" -WorkingDirectory $PSScriptRoot
    Write-Host "NOVA is starting. Look for the cat in the tray and peeking up at the bottom of the screen."
}
