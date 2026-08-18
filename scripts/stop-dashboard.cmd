@echo off
REM Stop the NoeRelay gateway server
REM Usage: stop-dashboard.cmd

echo Stopping NoeRelay gateway...

REM Find and kill the process listening on port 8080
for /f "tokens=5" %%a in ('netstat -aon ^| findstr /r ":8080.*LISTENING"') do (
    echo Killing process %%a ^(listening on port 8080^)
    taskkill /F /PID %%a >nul 2>&1
)

echo NoeRelay gateway stopped.
