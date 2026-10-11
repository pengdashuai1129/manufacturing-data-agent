@echo off

net session >nul 2>&1
if errorlevel 1 (
    echo This script must be run as Administrator.
    echo Right-click it and choose "Run as administrator".
    echo.
    pause
    exit /b 1
)

netsh advfirewall firewall delete rule name="ManufacturingDataAgent-8000" >nul 2>&1

netsh advfirewall firewall add rule ^
    name="ManufacturingDataAgent-8000" ^
    dir=in action=allow protocol=TCP localport=8000 ^
    profile=domain,private ^
    description="manufacturing-data-agent NL2SQL Q&A - LAN access"

if errorlevel 1 (
    echo.
    echo FAILED to add the rule.
) else (
    echo.
    echo OK - inbound TCP 8000 is now allowed on domain/private networks.
)

echo.
pause
