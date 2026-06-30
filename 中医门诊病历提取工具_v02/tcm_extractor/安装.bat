@echo off
chcp 65001 >nul 2>&1
title 中医门诊病历提取工具 - 安装

echo ============================================================
echo   中医门诊病历图像提取工具 - 环境安装
echo ============================================================
echo.

:: 检查 Python 是否已安装
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [错误] 未检测到 Python，请先安装 Python 3.10 或更高版本。
    echo 下载地址: https://www.python.org/downloads/
    echo.
    echo 安装时请勾选 "Add Python to PATH" 选项。
    pause
    exit /b 1
)

:: 显示 Python 版本
echo [1/3] 检测到 Python:
python --version
echo.

:: 创建虚拟环境（如果不存在）
if not exist "venv" (
    echo [2/3] 正在创建虚拟环境...
    python -m venv venv
    if %errorlevel% neq 0 (
        echo [错误] 创建虚拟环境失败。
        pause
        exit /b 1
    )
    echo       虚拟环境创建成功。
) else (
    echo [2/3] 虚拟环境已存在，跳过创建。
)
echo.

:: 激活虚拟环境并安装依赖
echo [3/3] 正在安装依赖包...
call venv\Scripts\activate.bat
pip install -r requirements.txt
if %errorlevel% neq 0 (
    echo [错误] 依赖安装失败，请检查网络连接后重试。
    pause
    exit /b 1
)

echo.
echo ============================================================
echo   安装完成！
echo.
echo   使用方法:
echo     1. 双击 "运行提取.bat" 选择图片文件夹
echo     2. 或运行: run.bat 图片文件夹路径
echo ============================================================
echo.
pause
