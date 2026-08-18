@echo off
REM Stop the NoeRelay gateway server
REM Usage: stop-dashboard.cmd

echo Stopping NoeRelay gateway...

REM Find and kill the Python process running the gateway
for /f "tokens=5" %%a in ('netstat -aon ^| findstr ":8080.*LISTENING"') do (
    echo Killing process %%a (listening on port 8080)
    taskkill /F /PID %%a 2>nul
)

REM Also kill any python process running -m gateway
for /f "tokens=2" %%a in ('tasklist /FI "IMAGENAME eq python.exe" /FO CSV /NH 2^>nul') do (
    for /f "tokens=1" %%b in ('wmic process where "ProcessId=%%a" get CommandLine 2^>nul ^| findstr "gateway"') do (
        echo Killing gateway process %%a
        taskkill /F /PID %%a 2>nul
    )
)

echo NoeRelay gateway stopped.