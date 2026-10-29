$ErrorActionPreference='Stop'
$taskRoot=Split-Path $PSScriptRoot -Parent
$sdk=Join-Path $taskRoot '.validation-deps\android'
$build=Join-Path $taskRoot '.validation-deps\layout-build'
$tools=Join-Path $sdk 'build-tools\35.0.0'
$androidJar=Join-Path $sdk 'platforms\android-35\android.jar'
[void][IO.Directory]::CreateDirectory($build)
[void][IO.Directory]::CreateDirectory((Join-Path $build 'classes'))
& javac -encoding UTF-8 -source 8 -target 8 -classpath $androidJar -d (Join-Path $build 'classes') (Join-Path $PSScriptRoot 'android_layout\LayoutInstrumentation.java') 2> (Join-Path $build 'javac.log')
if($LASTEXITCODE -ne 0){Get-Content (Join-Path $build 'javac.log') -Tail 8;throw 'Layout bridge compilation failed'}
& (Join-Path $tools 'aapt2.exe') link --manifest (Join-Path $PSScriptRoot 'android_layout\AndroidManifest.xml') -I $androidJar -o (Join-Path $build 'unsigned.apk')
if($LASTEXITCODE -ne 0){throw 'Android package assembly failed'}
$classes=@(Get-ChildItem (Join-Path $build 'classes') -Filter '*.class' -Recurse | ForEach-Object FullName)
& (Join-Path $tools 'd8.bat') --min-api 26 --lib $androidJar --output $build @classes 2> (Join-Path $build 'd8.log')
if($LASTEXITCODE -ne 0){Get-Content (Join-Path $build 'd8.log') -Tail 8;throw 'Android bytecode build failed'}
$python=Join-Path $taskRoot '.validation-deps\device-runtime\Scripts\python.exe'
& $python -c 'import sys,zipfile,pathlib;p=pathlib.Path(sys.argv[1]);z=zipfile.ZipFile(p/"unsigned.apk","a");z.write(p/"classes.dex","classes.dex");z.close()' $build
$keystore=Join-Path $taskRoot '.validation-deps\fixture-build\debug.jks'
& (Join-Path $tools 'apksigner.bat') sign --ks $keystore --ks-key-alias androiddebugkey --ks-pass pass:android --key-pass pass:android --out (Join-Path $build 'layout.apk') (Join-Path $build 'unsigned.apk')
if($LASTEXITCODE -ne 0){throw 'Local debug package signing failed'}
Write-Output 'Local read-only layout bridge built.'
