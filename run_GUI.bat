@echo off
REM Run ISO2GOD-RS standalone executable if exists, else fallback to python
SETLOCAL
SET SCRIPT_DIR=%~dp0
IF EXIST "%SCRIPT_DIR%ISO2GOD-RS.exe" (
    start "" "%SCRIPT_DIR%ISO2GOD-RS.exe"
) ELSE IF EXIST "%SCRIPT_DIR%pythonw.exe" (
    start "" "%SCRIPT_DIR%pythonw.exe" "%SCRIPT_DIR%gui.py"
) ELSE (
    python "%SCRIPT_DIR%gui.py"
)
ENDLOCAL