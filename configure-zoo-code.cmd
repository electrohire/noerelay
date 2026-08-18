@echo off
setlocal
set "ELECTROHIRE_PROJECT_ROOT=%~dp0."
where electrohire-zoo-code >nul 2>&1
if %ERRORLEVEL% equ 0 (
    electrohire-zoo-code --project "%ELECTROHIRE_PROJECT_ROOT%" %*
    exit /b %ERRORLEVEL%
)
set "ELECTROHIRE_ZOO_TOOL=%~dp0..\zoo-code-configurator\configure-zoo-code.cmd"
if exist "%ELECTROHIRE_ZOO_TOOL%" (
    call "%ELECTROHIRE_ZOO_TOOL%" --project "%ELECTROHIRE_PROJECT_ROOT%" %*
    exit /b %ERRORLEVEL%
)
set "ELECTROHIRE_ZOO_TOOL=%~dp0..\..\zoo-code-configurator\configure-zoo-code.cmd"
if exist "%ELECTROHIRE_ZOO_TOOL%" (
    call "%ELECTROHIRE_ZOO_TOOL%" --project "%ELECTROHIRE_PROJECT_ROOT%" %*
    exit /b %ERRORLEVEL%
)
echo ElectroHire Zoo Code Configurator was not found. 1>&2
echo Clone https://github.com/ElectroHire/zoo-code-configurator beside this repository or install it with pip. 1>&2
exit /b 1
