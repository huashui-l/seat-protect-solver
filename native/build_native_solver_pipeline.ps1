param(
    [Parameter(Mandatory = $true)]
    [string]$HighsRoot,
    [string]$OutputDir = "",
    [string]$Vswhere = ""
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
$Kernel = Join-Path $PSScriptRoot "native_pattern_kernel.cpp"
$Master = Join-Path $PSScriptRoot "native_restricted_master.cpp"
$Pipeline = Join-Path $PSScriptRoot "native_solver_pipeline.cpp"
$MonotonicPipeline = Join-Path $PSScriptRoot "native_monotonic_pipeline.cpp"
$MasterBenchmark = Join-Path $PSScriptRoot "native_master_benchmark.cpp"
$PoolExporter = Join-Path $PSScriptRoot "native_pool_exporter.cpp"
$FullCore = Join-Path $PSScriptRoot "full_cpp_solver_core.cpp"
$FullCoreProbe = Join-Path $PSScriptRoot "native_full_core_probe.cpp"
$FeasibilitySolver = Join-Path $PSScriptRoot "native_feasibility_solver.cpp"
$GroupConstructor = Join-Path $PSScriptRoot "native_group_constructor.cpp"
$RichPatternAdapter = Join-Path $PSScriptRoot "native_rich_pattern_adapter.cpp"
$SeatProtectCli = Join-Path $PSScriptRoot "seat_protect_cpp.cpp"
$StateReplay = Join-Path $PSScriptRoot "native_state_replay.cpp"
$KernelObject = Join-Path $OutputDir "native_pattern_kernel_library.obj"
$MasterObject = Join-Path $OutputDir "native_restricted_master_library.obj"
$PipelineObject = Join-Path $OutputDir "native_solver_pipeline.obj"
$Output = Join-Path $OutputDir "native_solver_pipeline.exe"
$MonotonicPipelineObject = Join-Path $OutputDir "native_monotonic_pipeline.obj"
$MonotonicOutput = Join-Path $OutputDir "native_monotonic_pipeline.exe"
$MasterBenchmarkObject = Join-Path $OutputDir "native_master_benchmark.obj"
$MasterBenchmarkOutput = Join-Path $OutputDir "native_master_benchmark.exe"
$PoolExporterObject = Join-Path $OutputDir "native_pool_exporter.obj"
$PoolExporterOutput = Join-Path $OutputDir "native_pool_exporter.exe"
$KernelStandaloneObject = Join-Path $OutputDir "native_pattern_kernel.obj"
$KernelStandaloneOutput = Join-Path $OutputDir "native_pattern_kernel.exe"
$FullCoreObject = Join-Path $OutputDir "full_cpp_solver_core.obj"
$FullCoreProbeObject = Join-Path $OutputDir "native_full_core_probe.obj"
$FullCoreProbeOutput = Join-Path $OutputDir "native_full_core_probe.exe"
$FeasibilitySolverObject = Join-Path $OutputDir "native_feasibility_solver.obj"
$GroupConstructorObject = Join-Path $OutputDir "native_group_constructor.obj"
$RichPatternAdapterObject = Join-Path $OutputDir "native_rich_pattern_adapter.obj"
$SeatProtectCliObject = Join-Path $OutputDir "seat_protect_cpp.obj"
$SeatProtectCliOutput = Join-Path $OutputDir "seat_protect_cpp.exe"
$StateReplayObject = Join-Path $OutputDir "native_state_replay.obj"
$StateReplayOutput = Join-Path $OutputDir "native_state_replay.exe"
$Include = Join-Path $HighsRoot "include"
$HighsInclude = Join-Path $Include "highs"
$Library = Join-Path $HighsRoot "lib"
$Dll = Join-Path $HighsRoot "bin\highs.dll"

$Compile = (
    'call "{0}" -arch=x64 -host_arch=x64 >nul && ' +
    'cl /nologo /O2 /EHsc /W4 /std:c++17 /c /DNATIVE_PATTERN_KERNEL_LIBRARY /Fo"{1}" "{2}" && ' +
    'cl /nologo /O2 /EHsc /W4 /std:c++17 /c /DNATIVE_RESTRICTED_MASTER_LIBRARY /Fo"{3}" "{4}" /I"{5}" /I"{6}" && ' +
    'cl /nologo /O2 /EHsc /W4 /std:c++17 /c /Fo"{7}" "{8}" && ' +
    'link /nologo "{1}" "{3}" "{7}" /LIBPATH:"{9}" highs.lib /OUT:"{10}" && ' +
    'cl /nologo /O2 /EHsc /W4 /std:c++17 /c /Fo"{11}" "{12}" && ' +
    'link /nologo "{1}" "{3}" "{11}" /LIBPATH:"{9}" highs.lib /OUT:"{13}"'
) -f $DevCmd, $KernelObject, $Kernel, $MasterObject, $Master, $Include, `
    $HighsInclude, $PipelineObject, $Pipeline, $Library, $Output, `
    $MonotonicPipelineObject, $MonotonicPipeline, $MonotonicOutput
& cmd.exe /d /c $Compile
if ($LASTEXITCODE -ne 0) {
    throw "native solver pipeline compilation failed"
}
$BenchmarkCompile = (
    'call "{0}" -arch=x64 -host_arch=x64 >nul && ' +
    'cl /nologo /O2 /EHsc /W4 /std:c++17 /c /Fo"{1}" "{2}" && ' +
    'link /nologo "{3}" "{1}" /LIBPATH:"{4}" highs.lib /OUT:"{5}"'
) -f $DevCmd, $MasterBenchmarkObject, $MasterBenchmark, $MasterObject, `
    $Library, $MasterBenchmarkOutput
& cmd.exe /d /c $BenchmarkCompile
if ($LASTEXITCODE -ne 0) {
    throw "native master benchmark compilation failed"
}
$PoolExporterCompile = (
    'call "{0}" -arch=x64 -host_arch=x64 >nul && ' +
    'cl /nologo /O2 /EHsc /W4 /std:c++17 /c /Fo"{1}" "{2}" && ' +
    'link /nologo "{3}" "{1}" /OUT:"{4}"'
) -f $DevCmd, $PoolExporterObject, $PoolExporter, $KernelObject, `
    $PoolExporterOutput
& cmd.exe /d /c $PoolExporterCompile
if ($LASTEXITCODE -ne 0) {
    throw "native pool exporter compilation failed"
}
$KernelCompile = (
    'call "{0}" -arch=x64 -host_arch=x64 >nul && ' +
    'cl /nologo /O2 /EHsc /W4 /std:c++17 /Fo"{1}" "{2}" /Fe"{3}"'
) -f $DevCmd, $KernelStandaloneObject, $Kernel, $KernelStandaloneOutput
& cmd.exe /d /c $KernelCompile
if ($LASTEXITCODE -ne 0) {
    throw "native pattern kernel standalone compilation failed"
}
$FullCoreProbeCompile = (
    'call "{0}" -arch=x64 -host_arch=x64 >nul && ' +
    'cl /nologo /O2 /EHsc /W4 /std:c++17 /c /Fo"{1}" "{2}" && ' +
    'cl /nologo /O2 /EHsc /W4 /std:c++17 /c /Fo"{3}" "{4}" && ' +
    'link /nologo "{1}" "{3}" /OUT:"{5}"'
) -f $DevCmd, $FullCoreObject, $FullCore, $FullCoreProbeObject, `
    $FullCoreProbe, $FullCoreProbeOutput
& cmd.exe /d /c $FullCoreProbeCompile
if ($LASTEXITCODE -ne 0) {
    throw "native full core probe compilation failed"
}
$SeatProtectCliCompile = (
    'call "{0}" -arch=x64 -host_arch=x64 >nul && ' +
    'cl /nologo /O2 /EHsc /W4 /std:c++17 /c /Fo"{1}" "{2}" /I"{3}" /I"{4}" && ' +
    'cl /nologo /O2 /EHsc /W4 /std:c++17 /c /Fo"{5}" "{6}" && ' +
    'cl /nologo /O2 /EHsc /W4 /std:c++17 /c /Fo"{7}" "{8}" && ' +
    'cl /nologo /O2 /EHsc /W4 /std:c++17 /c /Fo"{9}" "{10}" && ' +
    'link /nologo "{11}" "{1}" "{5}" "{7}" "{9}" "{12}" "{13}" /LIBPATH:"{14}" highs.lib /OUT:"{15}"'
) -f $DevCmd, $FeasibilitySolverObject, $FeasibilitySolver, $Include, `
    $HighsInclude, $SeatProtectCliObject, $SeatProtectCli, `
    $GroupConstructorObject, $GroupConstructor, $RichPatternAdapterObject, $RichPatternAdapter, `
    $FullCoreObject, $KernelObject, $MasterObject, $Library, $SeatProtectCliOutput
& cmd.exe /d /c $SeatProtectCliCompile
if ($LASTEXITCODE -ne 0) {
    throw "raw-native seat-protection CLI compilation failed"
}
$RepairProbeObject = Join-Path $OutputDir "native_rich_repair_probe.obj"
$RepairProbeSource = Join-Path $PSScriptRoot "native_rich_repair_probe.cpp"
$RepairProbeOutput = Join-Path $OutputDir "native_rich_repair_probe.exe"
$RepairProbeCompile = (
    'call "{0}" -arch=x64 -host_arch=x64 >nul && ' +
    'cl /nologo /O2 /EHsc /W4 /std:c++17 /c /Fo"{1}" "{2}" && ' +
    'link /nologo "{1}" "{3}" "{4}" "{5}" "{6}" "{7}" "{8}" /LIBPATH:"{9}" highs.lib /OUT:"{10}"'
) -f $DevCmd, $RepairProbeObject, $RepairProbeSource, $FullCoreObject, `
    $GroupConstructorObject, $FeasibilitySolverObject, $RichPatternAdapterObject, `
    $KernelObject, $MasterObject, $Library, $RepairProbeOutput
& cmd.exe /d /c $RepairProbeCompile
if ($LASTEXITCODE -ne 0) {
    throw "native Rich repair probe compilation failed"
}
$StateReplayCompile = (
    'call "{0}" -arch=x64 -host_arch=x64 >nul && ' +
    'cl /nologo /O2 /EHsc /W4 /std:c++17 /c /Fo"{1}" "{2}" && ' +
    'link /nologo "{3}" "{1}" /OUT:"{4}"'
) -f $DevCmd, $StateReplayObject, $StateReplay, $FullCoreObject, $StateReplayOutput
& cmd.exe /d /c $StateReplayCompile
if ($LASTEXITCODE -ne 0) {
    throw "native state replay compilation failed"
}
Copy-Item -LiteralPath $Dll -Destination (Join-Path $OutputDir "highs.dll") -Force
Write-Host "built=$Output highs=$HighsRoot"
Write-Host "built=$MonotonicOutput"
Write-Host "built=$MasterBenchmarkOutput"
Write-Host "built=$PoolExporterOutput"
Write-Host "built=$KernelStandaloneOutput"
Write-Host "built=$FullCoreProbeOutput"
Write-Host "built=$SeatProtectCliOutput"
Write-Host "built=$StateReplayOutput"
Write-Host "built=$RepairProbeOutput"
