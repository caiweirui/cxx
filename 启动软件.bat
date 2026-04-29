@echo off
chcp 65001 >nul
title CXXCrafter 可视化软件

:: 激活虚拟环境
call .venv\Scripts\activate.bat

:: 启动GUI
python src\cxxcrafter\gui\main.py

pause