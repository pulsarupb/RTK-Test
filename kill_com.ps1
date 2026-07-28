Write-Host "Looking for processes using COM8..."

# Check common serial port apps
$targets = @("u-center", "u-cent", "arduino", "putty", "teraterm", "python")
foreach ($proc in Get-Process | Where-Object { $_.ProcessName -match ($targets -join '|') }) {
    Write-Host ("  Found: " + $proc.ProcessName + " (PID: " + $proc.Id + ") " + $proc.MainWindowTitle)
}

# List all COM ports
$ports = [System.IO.Ports.SerialPort]::GetPortNames()
Write-Host "Available COM ports: $ports"

# Try to forcefully kill python processes that might hold it
$pythonProcs = Get-Process -Name "python" -ErrorAction SilentlyContinue
if ($pythonProcs) {
    Write-Host "Python processes found:"
    $pythonProcs | Select-Object Id, ProcessName, MainWindowTitle
}

Write-Host ""
Write-Host "To kill ALL python processes (if nothing else is using them):"
Write-Host "  Stop-Process -Name python -Force"
Write-Host ""
Write-Host "To kill specific PID:"
Write-Host "  Stop-Process -Id <PID> -Force"
