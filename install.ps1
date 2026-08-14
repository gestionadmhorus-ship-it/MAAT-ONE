[CmdletBinding()]
param()

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$installRoot = [System.IO.Path]::GetFullPath($PSScriptRoot)
$venvRoot = Join-Path $installRoot ".venv"
$venvPython = Join-Path $venvRoot "Scripts\python.exe"
$venvPythonw = Join-Path $venvRoot "Scripts\pythonw.exe"
$requirements = Join-Path $installRoot "requirements.txt"
$launcher = Join-Path $installRoot "launcher.pyw"
$application = Join-Path $installRoot "app.py"
$versionFile = Join-Path $installRoot "version.json"

function Write-Step([string]$Message) {
    Write-Host "`n==> $Message" -ForegroundColor Cyan
}

function Test-PythonCommand {
    param(
        [Parameter(Mandatory)] [string]$Executable,
        [string[]]$PrefixArguments = @()
    )

    $probe = "import platform,sys; print(f'{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}|{platform.architecture()[0]}')"
    $output = & $Executable @PrefixArguments -c $probe 2>$null
    if ($LASTEXITCODE -ne 0 -or -not $output) {
        return $null
    }
    $parts = ([string]$output).Trim().Split("|")
    if ($parts.Count -ne 2) {
        return $null
    }
    $version = [version]$parts[0]
    if ($version -lt [version]"3.10.0") {
        return $null
    }
    return [pscustomobject]@{
        Executable = $Executable
        Arguments = $PrefixArguments
        Version = $version
        Architecture = $parts[1]
    }
}

function Find-CompatiblePython {
    $pyLauncher = Get-Command "py.exe" -ErrorAction SilentlyContinue |
        Select-Object -First 1
    if ($null -ne $pyLauncher) {
        foreach ($selector in @("-3.14-64", "-3.14", "-3")) {
            $candidate = Test-PythonCommand $pyLauncher.Source @($selector)
            if ($null -ne $candidate) {
                return $candidate
            }
        }
    }

    $python = Get-Command "python.exe" -ErrorAction SilentlyContinue |
        Select-Object -First 1
    if ($null -ne $python) {
        return Test-PythonCommand $python.Source
    }
    return $null
}

function Invoke-CheckedPython {
    param(
        [Parameter(Mandatory)] [string]$Executable,
        [Parameter(Mandatory)] [string[]]$Arguments,
        [Parameter(Mandatory)] [string]$FailureMessage
    )
    & $Executable @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw $FailureMessage
    }
}

try {
    Write-Step "Validando archivos de instalación"
    foreach ($requiredFile in @(
        $requirements, $launcher, $application, $versionFile
    )) {
        if (-not (Test-Path -LiteralPath $requiredFile -PathType Leaf)) {
            throw "Falta un archivo necesario: $([System.IO.Path]::GetFileName($requiredFile))"
        }
    }
    $versionData = Get-Content -Raw -LiteralPath $versionFile |
        ConvertFrom-Json
    $localVersion = [string]$versionData.version
    if ($localVersion -notmatch '^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$') {
        throw "version.json no contiene una versión SemVer válida."
    }

    Write-Step "Detectando Python 3.10 o superior"
    $basePython = Find-CompatiblePython
    if ($null -eq $basePython) {
        throw "No se encontró Python 3.10 o superior. Instale Python para Windows y vuelva a ejecutar este instalador."
    }
    Write-Host "Python detectado: $($basePython.Version) ($($basePython.Architecture))"

    Write-Step "Preparando el entorno virtual local"
    if (Test-Path -LiteralPath $venvRoot) {
        if (-not (Test-Path -LiteralPath $venvPython -PathType Leaf) -or
            -not (Test-Path -LiteralPath $venvPythonw -PathType Leaf)) {
            throw "La carpeta .venv existente no es utilizable. Renómbrela o elimínela manualmente y vuelva a ejecutar el instalador."
        }
        $localPython = Test-PythonCommand $venvPython
        if ($null -eq $localPython) {
            throw "El entorno virtual .venv existente no contiene un Python compatible. Renómbrelo o elimínelo manualmente."
        }
        Write-Host "Se reutilizará el entorno virtual existente."
    }
    else {
        Invoke-CheckedPython `
            -Executable $basePython.Executable `
            -Arguments @($basePython.Arguments + @("-m", "venv", $venvRoot)) `
            -FailureMessage "No fue posible crear el entorno virtual .venv."
        if (-not (Test-Path -LiteralPath $venvPython -PathType Leaf) -or
            -not (Test-Path -LiteralPath $venvPythonw -PathType Leaf)) {
            throw "El entorno virtual no se creó correctamente."
        }
        Write-Host "Entorno virtual creado."
    }

    Write-Step "Instalando dependencias en .venv"
    Invoke-CheckedPython `
        -Executable $venvPython `
        -Arguments @("-m", "pip", "install", "-r", $requirements) `
        -FailureMessage "No fue posible instalar las dependencias. Revise la conexión y los mensajes anteriores."

    Write-Step "Validando MAAT One"
    $validationCode = @'
import json
import re
import tkinter
from pathlib import Path
import PIL
import openpyxl
import python_calamine
import app
from src.ui.main_window import MainWindow
from src.geographic_search import find_images_by_name
from src.geographic_selection import GeographicSelection
from src.geographic_export import export_geographic_selection
from src.update_service import migrate_flat_installation, read_current_state

root = Path.cwd()
data = json.loads((root / "version.json").read_text(encoding="utf-8"))
version = data.get("version")
if not isinstance(version, str) or re.fullmatch(r"(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)", version) is None:
    raise RuntimeError("version.json no contiene una versión SemVer válida")
active_root = migrate_flat_installation(root)
state = read_current_state(root)
if state["active_version"] != version or active_root.name != version:
    raise RuntimeError("La estructura versionada inicial no es coherente")
print(state["active_version"])
'@
    Push-Location $installRoot
    try {
        $validationCode | & $venvPython -
        if ($LASTEXITCODE -ne 0) {
            throw "La validación de MAAT One o sus dependencias falló."
        }
    }
    finally {
        Pop-Location
    }

    Write-Step "Creando el acceso directo MAAT One"
    $desktop = [Environment]::GetFolderPath("Desktop")
    if (-not $desktop) {
        throw "No fue posible localizar el Escritorio del usuario actual."
    }
    $shortcutPath = Join-Path $desktop "MAAT One.lnk"
    $temporaryShortcut = Join-Path $installRoot (
        ".maat-one-shortcut-{0}.lnk" -f [guid]::NewGuid().ToString("N")
    )
    try {
        $shell = New-Object -ComObject WScript.Shell
        $shortcut = $shell.CreateShortcut($temporaryShortcut)
        $shortcut.TargetPath = $venvPythonw
        $shortcut.Arguments = '"{0}"' -f $launcher
        $shortcut.WorkingDirectory = $installRoot
        $shortcut.Description = "MAAT One"
        $shortcut.WindowStyle = 1
        $shortcut.Save()
        if (-not (Test-Path -LiteralPath $temporaryShortcut -PathType Leaf)) {
            throw "No fue posible preparar el acceso directo."
        }
        Move-Item -LiteralPath $temporaryShortcut -Destination $shortcutPath -Force
    }
    finally {
        if (Test-Path -LiteralPath $temporaryShortcut) {
            Remove-Item -LiteralPath $temporaryShortcut -Force
        }
    }

    Write-Host "`nMAAT One $localVersion está listo." -ForegroundColor Green
    Write-Host "Acceso directo: $shortcutPath"
}
catch {
    Write-Host "`nLa instalación no pudo completarse:" -ForegroundColor Red
    Write-Host $_.Exception.Message -ForegroundColor Red
    exit 1
}
