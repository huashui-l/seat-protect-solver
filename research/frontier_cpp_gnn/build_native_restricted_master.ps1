param(
    [Parameter(Mandatory = $true)]
    [string]$HighsRoot,
    [string]$OutputDir = "",
    [string]$Vswhere = ""
)

$ErrorActionPreference = "Stop"
$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$HighsRoot = (Resolve-Path $HighsRoot).Path
if (-not $OutputDir) {
    $OutputDir = Join-Path $ProjectRoot "build\native"
}
New-Item -ItemType Directory -Path $OutputDir -Force | Out-Null
$OutputDir = (Resolve-Path $OutputDir).Path
if (-not $Vswhere) {
    $Vswhere = Join-Path ${env:ProgramFiles(x86)} `
        "Microsoft Visual Studio\Installer\vswhere.exe"
}
if (-not (Test-Path -LiteralPath $Vswhere)) {
    throw "Visual Studio vswhere.exe was not found"
}
$VsInstall = & $Vswhere -latest -products * `
    -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 `
    -property installationPath
if (-not $VsInstall) {
    throw "Visual Studio C++ x64 tools were not found"
}
$DevCmd = Join-Path $VsInstall "Common7\Tools\VsDevCmd.bat"
$Source = Join-Path $PSScriptRoot "native_restricted_master.cpp"
$Output = Join-Path $OutputDir "native_restricted_master.exe"
$Object = Join-Path $OutputDir "native_restricted_master.obj"
$Include = Join-Path $HighsRoot "include"
$HighsInclude = Join-Path $Include "highs"
$Library = Join-Path $HighsRoot "lib"
$Dll = Join-Path $HighsRoot "bin\highs.dll"

$Compile = (
    'call "{0}" -arch=x64 -host_arch=x64 >nul && ' +
    'cl /nologo /O2 /EHsc /W4 /std:c++17 /Fo"{6}" "{1}" ' +
    '/I"{2}" /I"{3}" /link /LIBPATH:"{4}" highs.lib /OUT:"{5}"'
) -f $DevCmd, $Source, $Include, $HighsInclude, $Library, $Output, $Object
& cmd.exe /d /c $Compile
if ($LASTEXITCODE -ne 0) {
    throw "native_restricted_master.cpp compilation failed"
}
Copy-Item -LiteralPath $Dll `
    -Destination (Join-Path (Split-Path $Output) "highs.dll") -Force
Write-Host "built=$Output highs=$HighsRoot"
