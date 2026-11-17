param(
    [string]$Distribution = 'Ubuntu',
    [string]$WslUser = ''
)

$ErrorActionPreference = 'Stop'
$project = Split-Path -Parent $PSScriptRoot
$launch = Join-Path $project 'launch.py'
$installer = Join-Path $PSScriptRoot 'install_wsl_launcher.py'
if (-not (Test-Path -LiteralPath $launch)) { throw 'launch.py is missing.' }
if ($Distribution -notmatch '^[A-Za-z0-9._-]+$') { throw 'Invalid distribution name.' }
if (-not $WslUser) {
    $WslUser = (& wsl.exe --distribution $Distribution --exec id -un | Out-String).Trim()
    if ($LASTEXITCODE -ne 0) { throw 'Cannot start the WSL distribution.' }
}
if ($WslUser -eq 'root' -or $WslUser -notmatch '^[a-z_][a-z0-9_-]*$') {
    throw 'Select a non-root Linux user with -WslUser.'
}
$linuxLaunch = (& wsl.exe --distribution $Distribution --user $WslUser --exec wslpath -a $launch | Out-String).Trim()
if ($LASTEXITCODE -ne 0) { throw 'Cannot translate the launcher path.' }
$linuxInstaller = (& wsl.exe --distribution $Distribution --user $WslUser --exec wslpath -a $installer | Out-String).Trim()
if ($LASTEXITCODE -ne 0) { throw 'Cannot translate the installer path.' }
if ($linuxLaunch -match '["%\r\n]') { throw 'Launcher path contains unsupported batch characters.' }

$bin = Join-Path $env:USERPROFILE '.local\bin'
$destination = Join-Path $bin 'nl2bash.cmd'
if ((Test-Path -LiteralPath $destination) -and
    ((Get-Content -LiteralPath $destination -Raw) -notmatch 'REM NL2BASH launcher')) {
    throw "Refusing to replace an unrelated command: $destination"
}
New-Item -ItemType Directory -Path $bin -Force | Out-Null
$content = "@echo off`r`nREM NL2BASH launcher`r`nwsl.exe --distribution $Distribution --user $WslUser --cd `"%CD%`" --exec /usr/bin/python3 `"$linuxLaunch`" %*`r`nexit /b %errorlevel%`r`n"
[IO.File]::WriteAllText($destination, $content, [Text.Encoding]::ASCII)

# Append only to this user's PATH and preserve all existing entries.
$userPath = [Environment]::GetEnvironmentVariable('Path', 'User')
$entries = @($userPath -split ';' | Where-Object { $_ })
$exists = @($entries | Where-Object { $_.TrimEnd('\') -ieq $bin.TrimEnd('\') }).Count -gt 0
if (-not $exists) {
    $newPath = ($entries + $bin) -join ';'
    [Environment]::SetEnvironmentVariable('Path', $newPath, 'User')
    Write-Output 'Added the launcher directory to your user PATH.'
} else {
    Write-Output 'Launcher directory is already in your user PATH.'
}

& wsl.exe --distribution $Distribution --user $WslUser --exec /usr/bin/python3 $linuxInstaller
if ($LASTEXITCODE -ne 0) { throw 'WSL launcher installation failed.' }
Write-Output "Installed Windows command: $destination"
Write-Output 'Open a new CMD or PowerShell window, enter any project folder, and run: nl2bash'
