# Sets up NOVA: creates .venv, installs packages, downloads the offline models.
# Run from the project folder:  powershell -ExecutionPolicy Bypass -File setup.ps1
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

$python = "C:\Users\Russell Magdaong\AppData\Local\Programs\Python\Python313\python.exe"
if (-not (Test-Path $python)) { $python = (Get-Command python).Source }

if (-not (Test-Path ".venv\Scripts\python.exe")) {
    Write-Host "Creating .venv..."
    & $python -m venv .venv
}
Write-Host "Installing packages..."
& .venv\Scripts\python.exe -m pip install --upgrade pip -q
& .venv\Scripts\python.exe -m pip install -r requirements.txt -q

if (Get-Command nvidia-smi -ErrorAction SilentlyContinue) {
    Write-Host "NVIDIA GPU found: installing the CUDA build of onnxruntime and NVIDIA's libraries (~1.9 GB)..."
    # The CPU and GPU builds share the onnxruntime folder: remove the CPU one, then reinstall the GPU one cleanly.
    & .venv\Scripts\python.exe -m pip uninstall -y onnxruntime -q
    & .venv\Scripts\python.exe -m pip install --force-reinstall --no-deps "onnxruntime-gpu==1.26.0" -q
    & .venv\Scripts\python.exe -m pip install -r requirements-gpu.txt -q
} else {
    Write-Host "No NVIDIA GPU: Kokoro and Whisper will run on the CPU (set [speech_to_text] device = ""cpu"")."
}

$model = "models\vosk-model-small-en-us-0.15"
if (-not (Test-Path $model)) {
    Write-Host "Downloading the Vosk wake-word model (~40 MB)..."
    New-Item -ItemType Directory -Force models | Out-Null
    $zip = "models\vosk-small.zip"
    Invoke-WebRequest "https://alphacephei.com/vosk/models/vosk-model-small-en-us-0.15.zip" -OutFile $zip
    Expand-Archive $zip -DestinationPath models -Force
    Remove-Item $zip
}

$kokoro = "models\kokoro"
if (-not (Test-Path "$kokoro\kokoro-v1.0.onnx")) {
    Write-Host "Downloading the Kokoro voice model (~340 MB)..."
    New-Item -ItemType Directory -Force $kokoro | Out-Null
    $base = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0"
    Invoke-WebRequest "$base/kokoro-v1.0.onnx" -OutFile "$kokoro\kokoro-v1.0.onnx"
    Invoke-WebRequest "$base/voices-v1.0.bin" -OutFile "$kokoro\voices-v1.0.bin"
}

Write-Host "Downloading the Whisper models (first run only; large-v3-turbo ~1.6 GB, base.en ~140 MB)..."
& .venv\Scripts\python.exe -c "from faster_whisper import download_model; download_model('large-v3-turbo'); download_model('base.en')"

Write-Host "`nDone. Start NOVA with:  .\start.ps1"
