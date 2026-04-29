#!/bin/bash

# CXXCrafter Ubuntu命令行运行脚本
echo "=========================================="
echo "CXXCrafter 命令行模式 - Ubuntu"
echo "=========================================="

# 激活虚拟环境
source .venv/bin/activate

# 运行
python src/cxxcrafter/run.py "$@"