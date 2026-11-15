param(
    [string]$ConfigFile = (Join-Path (Split-Path -Parent $PSScriptRoot) '.env.alerts'),
    [string]$ThroughMonth = ''
)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
if (-not (Test-Path -LiteralPath $ConfigFile -PathType Leaf)) {
    throw 'Falta el archivo local de configuración de alertas. Ver README y .env.example.'
}
# Valores literales y variables del proceso: no evalúa código ni cambia el entorno global.
foreach ($configLine in Get-Content -LiteralPath $ConfigFile -Encoding UTF8) {
    if ($configLine -match '^\s*(ALERT_[A-Z0-9_]+)\s*=(.*)$') {
        $variableName = $Matches[1]
        $variableValue = $Matches[2].Trim().Trim('"').Trim("'")
        Set-Item -LiteralPath ('Env:' + $variableName) -Value $variableValue
    }
}
$pythonPath = Join-Path $projectRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $pythonPath -PathType Leaf)) { throw 'Falta el entorno virtual del proyecto.' }
$workerArguments = @('-B', '-m', 'notifications', 'run', '--allow-database-access')
if ($ThroughMonth) { $workerArguments += @('--through-month', $ThroughMonth) }
Push-Location -LiteralPath $projectRoot
try {
    & $pythonPath @workerArguments
    exit $LASTEXITCODE
} finally {
    Pop-Location
}
