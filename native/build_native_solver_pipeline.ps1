param(
    [Parameter(Mandatory = $true)]
    [string]$HighsRoot,
    [string]$OutputDir = "",
    [string]$Vswhere = "",
    [switch]$IncludeTools
)

$ErrorActionPreference = "Stop"
$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
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

$Include = Join-Path $HighsRoot "include"
$HighsInclude = Join-Path $Include "highs"
$Library = Join-Path $HighsRoot "lib"
$Commands = [System.Collections.Generic.List[string]]::new()
$Commands.Add(('call "{0}" -arch=x64 -host_arch=x64 >nul' -f $DevCmd))
function Add-Compile([string]$Name, [string]$Source = "", [string]$Define = "") {
    if (-not $Source) { $Source = $Name }
    $Commands.Add(('cl /nologo /O2 /EHsc /W4 /std:c++17 /c {0} /I"{1}" /I"{2}" /Fo"{3}\{4}.obj" "{5}\{6}.cpp"' -f
        $Define, $Include, $HighsInclude, $OutputDir, $Name, $PSScriptRoot, $Source))
}
function Add-Link([string]$Name, [string[]]$Objects) {
    $Inputs = ($Objects | ForEach-Object { '"{0}\{1}.obj"' -f $OutputDir, $_ }) -join ' '
    $Commands.Add(('link /nologo {0} /LIBPATH:"{1}" highs.lib /OUT:"{2}\{3}.exe"' -f
        $Inputs, $Library, $OutputDir, $Name))
}
$SolverObjects = @(
    'full_cpp_solver_core',
    'full_cpp_solver_core_input',
    'full_cpp_solver_core_scoring',
    'full_cpp_solver_core_rich_repair',
    'full_cpp_solver_core_rich_patterns',
    'full_cpp_solver_core_state',
    'full_cpp_solver_core_elite',
    'native_feasibility_solver',
    'native_group_constructor',
    'native_rich_construction'
)
foreach ($Object in $SolverObjects) { Add-Compile $Object }
Add-Compile 'seat_protect_cpp'
Add-Link 'seat_protect_cpp' ($SolverObjects + 'seat_protect_cpp')

# Probes and historical pool benchmarks are separate executables, never solver dependencies.
if ($IncludeTools) {
    foreach ($Probe in @('native_full_core_probe', 'native_state_replay')) {
        Add-Compile $Probe
        Add-Link $Probe ($SolverObjects + $Probe)
    }
    Add-Compile 'native_rich_repair_probe'
    Add-Link 'native_rich_repair_probe' ($SolverObjects + 'native_rich_repair_probe')
    Add-Compile 'native_pattern_kernel_library' 'native_pattern_kernel' '/DNATIVE_PATTERN_KERNEL_LIBRARY'
    Add-Compile 'native_restricted_master_library' 'native_restricted_master' '/DNATIVE_RESTRICTED_MASTER_LIBRARY'
    foreach ($Pipeline in @('native_solver_pipeline', 'native_monotonic_pipeline')) {
        Add-Compile $Pipeline
        Add-Link $Pipeline @('native_pattern_kernel_library', 'native_restricted_master_library', $Pipeline)
    }
    Add-Compile 'native_master_benchmark'
    Add-Link 'native_master_benchmark' @('native_restricted_master_library', 'native_master_benchmark')
    Add-Compile 'native_pool_exporter'
    Add-Link 'native_pool_exporter' @('native_pattern_kernel_library', 'native_pool_exporter')
    Add-Compile 'native_pattern_kernel'
    Add-Link 'native_pattern_kernel' @('native_pattern_kernel')
}
$BuildScript = Join-Path $OutputDir "compile_native.cmd"
$Lines = @("@echo off")
foreach ($Command in $Commands) {
    $Lines += $Command
    $Lines += "if errorlevel 1 exit /b 1"
}
Set-Content -LiteralPath $BuildScript -Value $Lines -Encoding Default
try {
    & cmd.exe /d /c ('"{0}"' -f $BuildScript)
    if ($LASTEXITCODE -ne 0) { throw "native compilation failed" }
} finally {
    Remove-Item -LiteralPath $BuildScript
}
Copy-Item -LiteralPath (Join-Path $HighsRoot "bin\highs.dll") -Destination (Join-Path $OutputDir "highs.dll") -Force
Write-Host "built=$OutputDir\seat_protect_cpp.exe include_tools=$IncludeTools highs=$HighsRoot"
