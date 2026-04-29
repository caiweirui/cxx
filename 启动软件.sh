#!/bin/bash

# CXXCrafter Ubuntu启动脚本
echo "=========================================="
echo "CXXCrafter 可视化软件 - Ubuntu"
echo "=========================================="

# 检查虚拟环境
if [ ! -d ".venv" ]; then
    echo "❌ 虚拟环境不存在，请先运行安装脚本！"
    exit 1
fi

# 激活虚拟环境
echo "✅ 激活虚拟环境..."
source .venv/bin/activate

# 启动GUI
echo "🚀 启动软件..."
python src/cxxcrafter/gui/main.py

echo "✅ 软件已退出"