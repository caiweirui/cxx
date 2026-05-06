@echo off
chcp 65001 >nul
setlocal EnableExtensions

title CXXCrafter 启动器

set "ROOT=%~dp0"
pushd "%ROOT%" || (
    echo [错误] 无法进入启动器目录：%ROOT%
    pause
    exit /b 1
)

set "PY_CMD="

REM 优先使用 python
python --version >nul 2>&1
if not errorlevel 1 (
    set "PY_CMD=python"
) else (
    REM 其次使用 py
    py --version >nul 2>&1
    if not errorlevel 1 (
        set "PY_CMD=py"
    )
)

if not defined PY_CMD (
    echo [错误] 未找到可用的 Python 环境。
    echo 请安装 Python 3.10+，并确保 python 或 py 命令可用。
    pause
    popd
    exit /b 1
)

echo 正在启动 GUI ...
echo.

"%PY_CMD%" "%ROOT%src\cxxcrafter\gui\main.py"

popd
endlocal
exit /b 0