@echo off
chcp 65001 >nul 2>&1
title 中医门诊病历提取工具 - 选择文件夹

:: 检查虚拟环境
if not exist "venv\Scripts\activate.bat" (
    echo [错误] 虚拟环境不存在，请先双击 "安装.bat" 进行安装。
    pause
    exit /b 1
)

:: 激活虚拟环境
call venv\Scripts\activate.bat

:: 用 Python 弹出文件夹选择对话框
echo 正在打开文件夹选择对话框...
for /f "delims=" %%i in ('python -c "import tkinter as tk; from tkinter import filedialog; root=tk.Tk(); root.withdraw(); root.attributes('-topmost', True); d=filedialog.askdirectory(title='请选择包含门诊截图的文件夹'); print(d if d else '')"') do set FOLDER=%%i

if "%FOLDER%"=="" (
    echo 未选择文件夹，已取消。
    pause
    exit /b 0
)

echo.
echo 已选择: %FOLDER%
echo 输出文件: %~dp0门诊记录.xlsx
echo.
echo 正在提取，请稍候...
echo ============================================================

python main.py "%FOLDER%" --output "%~dp0门诊记录.xlsx"

echo.
echo ============================================================
if %errorlevel% equ 0 (
    echo 处理完成！
    echo 输出文件: %~dp0门诊记录.xlsx
    echo.
    :: 自动打开输出文件所在目录
    explorer "%~dp0"
) else (
    echo [错误] 处理过程中出现错误，请检查上方日志。
)
pause
