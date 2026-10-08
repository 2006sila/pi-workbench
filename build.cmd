@echo off
chcp 65001 >nul
setlocal

set PY=%~dp0.venv\Scripts\python.exe
if not exist "%PY%" set PY=python

echo [1/2] 清理旧产物
if exist build rmdir /s /q build
if exist dist rmdir /s /q dist

echo [2/2] 使用 PyInstaller 构建 onedir
"%PY%" -m PyInstaller --noconfirm bj_tool.spec
if errorlevel 1 (
    echo 构建失败
    exit /b 1
)

echo.
echo 产物目录: %~dp0dist\pi用学习工作台\（onedir：整体拷贝即安装，exe 在目录里）
dir /b dist
endlocal
