# abzctl o'rnatuvchisi (Windows).
#
# Ishlatish: install.bat faylini ikki marta bosing, yoki PowerShell'da:
#     powershell -ExecutionPolicy Bypass -File install.ps1
#
# Nima qiladi:
#   1. Python 3.9+ borligini tekshiradi (yo'q bo'lsa winget orqali o'rnatadi)
#   2. %USERPROFILE%\.abzctl\venv ichiga abzctl va uning kutubxonalarini o'rnatadi
#   3. `abzctl` buyrug'ini PATH'ga qo'shadi
#
# Qayta ishga tushirilsa — abzctl yangilanadi.

$ErrorActionPreference = "Stop"

$SrcDir     = $PSScriptRoot
$InstallDir = Join-Path $env:USERPROFILE ".abzctl"
$VenvDir    = Join-Path $InstallDir "venv"
$BinDir     = Join-Path $InstallDir "bin"

function Write-Step($msg) { Write-Host "==> $msg" -ForegroundColor Cyan }

Write-Step "abzctl o'rnatilmoqda"

# --- 1. Python ---------------------------------------------------------------
function Find-Python {
    foreach ($candidate in @(@("py", "-3"), @("python"), @("python3"))) {
        $exe = $candidate[0]
        $rest = @($candidate | Select-Object -Skip 1)
        if (Get-Command $exe -ErrorAction SilentlyContinue) {
            & $exe @rest -c "import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)" 2>$null
            if ($LASTEXITCODE -eq 0) { return ,$candidate }
        }
    }
    return $null
}

$Py = Find-Python
if (-not $Py) {
    if (Get-Command winget -ErrorAction SilentlyContinue) {
        Write-Step "Python topilmadi, winget orqali o'rnatilmoqda"
        winget install -e --id Python.Python.3.12 --accept-source-agreements --accept-package-agreements
        # Yangi o'rnatilgan Python joriy oynada ko'rinishi uchun PATH'ni yangilaymiz.
        $env:Path = [Environment]::GetEnvironmentVariable("Path", "Machine") + ";" +
                    [Environment]::GetEnvironmentVariable("Path", "User")
        $Py = Find-Python
    }
    if (-not $Py) {
        Write-Host "Python 3.9+ topilmadi. https://www.python.org/downloads/ dan o'rnating" -ForegroundColor Red
        Write-Host "(o'rnatishda 'Add python.exe to PATH' belgisini qo'ying), so'ng install.bat ni qayta ishga tushiring." -ForegroundColor Red
        exit 1
    }
}
$PyExe  = $Py[0]
$PyArgs = @($Py | Select-Object -Skip 1)
Write-Host "    Python: $(& $PyExe @PyArgs --version)"

# --- 2. Virtual muhit va abzctl ---------------------------------------------
Write-Step "Kutubxonalar o'rnatilmoqda ($InstallDir)"
New-Item -ItemType Directory -Force -Path $InstallDir | Out-Null
& $PyExe @PyArgs -m venv $VenvDir
if ($LASTEXITCODE -ne 0) { throw "Virtual muhit yaratib bo'lmadi." }

$VenvPy = Join-Path $VenvDir "Scripts\python.exe"
& $VenvPy -m pip install -q --upgrade pip
if ($LASTEXITCODE -ne 0) { throw "pip'ni yangilab bo'lmadi." }
& $VenvPy -m pip install -q --upgrade $SrcDir
if ($LASTEXITCODE -ne 0) { throw "abzctl'ni o'rnatib bo'lmadi." }

# --- 3. Global buyruq --------------------------------------------------------
# venv\Scripts ni PATH'ga qo'shmaymiz (u yerdagi python.exe tizim Python'ini to'sib qo'yadi),
# o'rniga faqat abzctl.cmd yorlig'ini alohida papkaga qo'yamiz.
New-Item -ItemType Directory -Force -Path $BinDir | Out-Null
$Shim = Join-Path $BinDir "abzctl.cmd"
Set-Content -Path $Shim -Encoding ASCII -Value "@echo off`r`n`"$VenvDir\Scripts\abzctl.exe`" %*"

$UserPath = [Environment]::GetEnvironmentVariable("Path", "User")
if (-not $UserPath) { $UserPath = "" }
$PathAdded = $false
if (($UserPath -split ";") -notcontains $BinDir) {
    [Environment]::SetEnvironmentVariable("Path", ($UserPath.TrimEnd(";") + ";" + $BinDir).TrimStart(";"), "User")
    $PathAdded = $true
}

Write-Host ""
$InstalledVersion = & $Shim --version
if ($LASTEXITCODE -ne 0) { throw "abzctl o'rnatildi, ammo ishga tushmadi." }
Write-Host "✔ abzctl o'rnatildi ($InstalledVersion)" -ForegroundColor Green
Write-Host ""
if ($PathAdded) {
    Write-Host "  Yangi terminal oynasini oching (Windows Terminal yoki PowerShell), so'ng yozing:"
} else {
    Write-Host "  Deploy qilish uchun yozing:"
}
Write-Host ""
Write-Host "      abzctl" -ForegroundColor Green
Write-Host ""
