@echo off
chcp 65001 >nul 2>&1
title 中医门诊病历提取工具

:: 检查虚拟环境
if not exist "venv\Scripts\activate.bat" (
    echo [错误] 虚拟环境不存在，请先双击 "安装.bat" 进行安装。
    pause
    exit /b 1
)

:: 激活虚拟环境
call venv\Scripts\activate.bat

:: 检查参数
if "%~1"=="" (
    echo 用法: run.bat ^<图片文件夹路径^>
    echo.
    echo 示例:
    echo   run.bat C:\截图\门诊
    echo   run.bat "D:\我的文件\Case原始测试资料"
    echo.
    echo 提示: 也可以双击 "运行提取.bat" 通过图形界面选择文件夹。
    pause
    exit /b 0
)

:: 运行提取
echo 正在处理: %~1
echo 输出文件: %~dp0门诊记录.xlsx
echo.
python main.py "%~1" --output "%~dp0门诊记录.xlsx"

echo.
if %errorlevel% equ 0 (
    echo 处理完成！输出文件: 门诊记录.xlsx
) else (
    echo [错误] 处理过程中出现错误，请检查上方日志。
)
pause
