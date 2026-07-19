$ErrorActionPreference = "Stop"
Set-Location -LiteralPath $PSScriptRoot

$env:MPLCONFIGDIR = Join-Path $PSScriptRoot "work_full\matplotlib"
$env:PYANNOTE_METRICS_ENABLED = "0"
$env:HF_HUB_OFFLINE = "1"
$env:HF_DATASETS_OFFLINE = "1"
$env:PYTHONWARNINGS = "ignore"
$env:LOKY_MAX_CPU_COUNT = "16"

$serverExe = Join-Path $PSScriptRoot "tools\llama.cpp\llama-server.exe"
$model = Join-Path $PSScriptRoot "models\bielik-11b-v3.0-instruct\Bielik-11B-v3.0-Instruct.Q5_K_M.gguf"
$python = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
$cli = Join-Path $PSScriptRoot ".venv\Scripts\pbx-transcribe.exe"
$supervisorLog = Join-Path $PSScriptRoot "work_full\supervisor.log"
$serverOut = Join-Path $PSScriptRoot "work_full\llama-retry.stdout.log"
$serverErr = Join-Path $PSScriptRoot "work_full\llama-retry.stderr.log"

$server = Start-Process -FilePath $serverExe `
    -ArgumentList @("-m", $model, "--host", "127.0.0.1", "--port", "8080", "-ngl", "99", "-c", "8192", "--parallel", "1") `
    -WorkingDirectory (Split-Path -Parent $serverExe) `
    -RedirectStandardOutput $serverOut `
    -RedirectStandardError $serverErr `
    -WindowStyle Hidden `
    -PassThru

try {
    $ready = $false
    for ($attempt = 0; $attempt -lt 60; $attempt++) {
        if ($server.HasExited) {
            throw "Local LLM server exited during startup"
        }
        try {
            $health = Invoke-WebRequest -UseBasicParsing -Uri "http://127.0.0.1:8080/health" -TimeoutSec 2
            if ($health.StatusCode -eq 200) {
                $ready = $true
                break
            }
        } catch {
            Start-Sleep -Seconds 2
        }
    }
    if (-not $ready) {
        throw "Local LLM server did not become ready"
    }

    & $cli retry-interrupted *>> $supervisorLog
    & $cli worker *>> $supervisorLog
    if ($LASTEXITCODE -ne 0) {
        throw "Worker exited with a non-zero status"
    }
} catch {
    $safeType = $_.Exception.GetType().Name
    Add-Content -LiteralPath $supervisorLog -Value ("supervisor_error_type=" + $safeType)
    exit 1
} finally {
    if (-not $server.HasExited) {
        Stop-Process -Id $server.Id -Force -ErrorAction SilentlyContinue
    }
}
