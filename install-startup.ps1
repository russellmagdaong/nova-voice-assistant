# Adds (or removes) NOVA from Windows startup via a shortcut in your Startup folder.
#   powershell -ExecutionPolicy Bypass -File install-startup.ps1           add
#   powershell -ExecutionPolicy Bypass -File install-startup.ps1 -Remove   remove
param([switch]$Remove)
$startup = [Environment]::GetFolderPath("Startup")
$link = Join-Path $startup "NOVA.lnk"

if ($Remove) {
    Remove-Item $link -ErrorAction SilentlyContinue
    Write-Host "NOVA removed from startup."
    exit
}

$shell = New-Object -ComObject WScript.Shell
$s = $shell.CreateShortcut($link)
$s.TargetPath = Join-Path $PSScriptRoot ".venv\Scripts\pythonw.exe"
$s.Arguments = "-m nova"
$s.WorkingDirectory = $PSScriptRoot
$s.Description = "NOVA voice assistant"
$s.Save()
Write-Host "NOVA will start when you sign in. Shortcut: $link"
