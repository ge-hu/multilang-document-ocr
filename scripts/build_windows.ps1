$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot

python -m pip install --upgrade pip
python -m pip install -r requirements.txt

if (-not (Get-Command choco.exe -ErrorAction SilentlyContinue)) {
    throw "GitHub Windows runner未找到Chocolatey。"
}

choco install tesseract --yes --no-progress

$TesseractCandidates = @(
    "C:\Program Files\Tesseract-OCR\tesseract.exe",
    "C:\Program Files (x86)\Tesseract-OCR\tesseract.exe"
)
$TesseractExe = $TesseractCandidates | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $TesseractExe) {
    $TesseractExe = Get-ChildItem "C:\Program Files" -Filter "tesseract.exe" -Recurse -ErrorAction SilentlyContinue | Select-Object -First 1 -ExpandProperty FullName
}
if (-not $TesseractExe) {
    throw "Tesseract安装后仍未找到可执行文件。"
}

$VendorDir = Join-Path $ProjectRoot "vendor\tesseract"
if (Test-Path $VendorDir) {
    Remove-Item $VendorDir -Recurse -Force
}
New-Item $VendorDir -ItemType Directory -Force | Out-Null
Copy-Item (Join-Path (Split-Path $TesseractExe -Parent) "*") $VendorDir -Recurse -Force

$TessdataDir = Join-Path $VendorDir "tessdata"
New-Item $TessdataDir -ItemType Directory -Force | Out-Null
$Languages = @(
    "eng", "nld", "pol", "tur", "spa", "fra", "dan", "lit",
    "swe", "ron", "bul", "fin", "hrv", "lav", "ell", "por",
    "est", "deu", "slv", "slk", "ita", "ces", "hun", "chi_sim", "osd"
)
foreach ($Language in $Languages) {
    $Url = "https://raw.githubusercontent.com/tesseract-ocr/tessdata_fast/master/$Language.traineddata"
    $Destination = Join-Path $TessdataDir "$Language.traineddata"
    Write-Host "下载OCR语言包：$Language"
    Invoke-WebRequest -Uri $Url -OutFile $Destination
}

$AssetsDir = Join-Path $ProjectRoot "assets"
New-Item $AssetsDir -ItemType Directory -Force | Out-Null
$FontPath = Join-Path $AssetsDir "NotoSansSC-VF.ttf"
$FontUrl = "https://raw.githubusercontent.com/googlefonts/noto-cjk/main/Sans/Variable/TTF/Subset/NotoSansSC-VF.ttf"
Invoke-WebRequest -Uri $FontUrl -OutFile $FontPath

python -m unittest discover -s tests -v
python -m PyInstaller --noconfirm --clean MultilangOCR.spec

$PortableDir = Join-Path $ProjectRoot "dist\MultilangOCR-Portable"
if (-not (Test-Path (Join-Path $PortableDir "MultilangOCR.exe"))) {
    throw "便携版构建失败：未生成MultilangOCR.exe。"
}

$SelfTestLog = Join-Path $env:TEMP "MultilangOCR-self-test.log"
if (Test-Path $SelfTestLog) {
    Remove-Item $SelfTestLog -Force
}
$SelfTest = Start-Process (Join-Path $PortableDir "MultilangOCR.exe") -ArgumentList "--self-test" -Wait -PassThru
if ($SelfTest.ExitCode -ne 0) {
    if (Test-Path $SelfTestLog) {
        Get-Content $SelfTestLog
    }
    throw "打包后自检失败，退出代码：$($SelfTest.ExitCode)"
}
Write-Host "打包后OCR与A4 PDF自检通过。"

$SizeBytes = (Get-ChildItem $PortableDir -Recurse -File | Measure-Object Length -Sum).Sum
$SizeMB = [math]::Round($SizeBytes / 1MB, 1)
Write-Host "便携版构建完成，解压后约 $SizeMB MB。"
