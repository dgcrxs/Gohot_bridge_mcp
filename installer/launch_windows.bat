@echo off
chcp 65001 >nul
setlocal
rem Launch the installer from source (used when no .exe is available).
rem The repository root is the parent of this script's directory.
rem Drag & drop: dropping a Godot project folder onto this file passes the
rem folder path as the first argument; "%*" forwards it to the installer, which
rem treats a bare path exactly like --project <path> (GUI opens prefilled).
cd /d "%~dp0.."

set "PY="
where py >nul 2>nul
if not errorlevel 1 set "PY=py -3"
if not defined PY (
    where python >nul 2>nul
    if not errorlevel 1 set "PY=python"
)
if not defined PY (
    echo.
    echo [错误] 没有找到 Python 3.10 或更新版本。
    echo 请到 https://www.python.org/downloads/ 下载安装，
    echo 安装时务必勾选 "Add python.exe to PATH"，然后重新双击本文件。
    echo.
    pause
    exit /b 2
)

%PY% -m installer %*
set "CODE=%ERRORLEVEL%"
if not "%CODE%"=="0" (
    echo.
    echo 安装器退出码：%CODE%
    pause
)
exit /b %CODE%
