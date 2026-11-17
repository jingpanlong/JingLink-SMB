$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot
$out = Join-Path $PSScriptRoot 'outputs'
$work = Join-Path $PSScriptRoot '.build'
$appDist = Join-Path $work 'app-dist'
$portableName = 'JingLink-SMB-2.2-x64.exe'
$installerName = 'JingLink-SMB-2.2-Setup-x64'
New-Item -ItemType Directory -Force -Path $out,$work,$appDist | Out-Null
python -m pip install -r requirements.txt
if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed' }
python -m compileall -q smb-connect.py studio.py core.py localization.py setup.py
if ($LASTEXITCODE -ne 0) { throw 'Python validation failed' }
python -m PyInstaller --noconfirm --clean --onefile --windowed --name '景连SMB' --icon 'assets/app.ico' --add-data 'assets;assets' --collect-all customtkinter --collect-all pystray smb-connect.py --distpath $appDist --workpath "$work/app" --specpath $PSScriptRoot
if ($LASTEXITCODE -ne 0) { throw 'App build failed' }
python -m PyInstaller --noconfirm --clean --onefile --windowed --name $installerName --icon 'assets/app.ico' --add-data "$appDist/景连SMB.exe;." --add-data 'assets;assets' --collect-all customtkinter setup.py --distpath $out --workpath "$work/setup" --specpath $PSScriptRoot
if ($LASTEXITCODE -ne 0) { throw 'Installer build failed' }
Copy-Item (Join-Path $appDist "景连SMB.exe") (Join-Path $out $portableName) -Force
Get-Item "$out/$portableName", "$out/$installerName.exe" | Select-Object Name,Length
