# Compile NostroLab + NostroBridge against the NinjaTrader stand-in types (stubs\NtStubs.cs) — a syntax/API-shape check
# that works without NinjaTrader installed. NinjaTrader itself compiles the real thing (NinjaScript Editor, F5).
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$csc = "$env:WINDIR\Microsoft.NET\Framework64\v4.0.30319\csc.exe"
$out = Join-Path $env:TEMP "nostro_ntcheck.dll"
& $csc -nologo -target:library "-out:$out" -r:System.ComponentModel.DataAnnotations.dll `
    (Join-Path $here "stubs\NtStubs.cs") (Join-Path $here "Strategies\NostroLab.cs") (Join-Path $here "AddOns\NostroBridge.cs")
if ($LASTEXITCODE -eq 0) { Write-Output "OK: NinjaScript files compile"; Remove-Item $out -ErrorAction SilentlyContinue } else { exit 1 }
