@echo off
chcp 65001 >nul
setlocal

set PY=%~dp0.venv\Scripts\python.exe
if not exist "%PY%" set PY=python

echo [1/2] 清理旧产物
if exist build rmdir /s /q build
if exist dist rmdir /s /q dist

echo [2/2] 使用 PyInstaller 构建单文件
"%PY%" -m PyInstaller --noconfirm bj_tool.spec
if errorlevel 1 (
    echo 构建失败
    exit /b 1
)

echo.
echo 产物: %~dp0dist\pi用学习工作台.exe
dir /b dist
endlocal
