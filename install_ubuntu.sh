#!/bin/bash

# CXXCrafter Ubuntu一键安装脚本
echo "=========================================="
echo "CXXCrafter Ubuntu环境安装"
echo "=========================================="

# 1. 安装系统级依赖（🔥 新增：python3-tk）
echo "📦 安装系统级依赖..."
sudo apt-get update
sudo apt-get install -y \
    python3 \
    python3-pip \
    python3-venv \
    python3-tk \
    build-essential \
    git \
    xdg-utils \
    curl

# 2. 创建虚拟环境
echo "🐍 创建Python虚拟环境..."
if [ -d ".venv" ]; then
    echo "⚠️  虚拟环境已存在，跳过创建"
else
    python3 -m venv .venv
fi

# 3. 激活虚拟环境
echo "✅ 激活虚拟环境..."
source .venv/bin/activate

# 4. 升级pip
echo "⬆️  升级pip..."
pip install --upgrade pip

# 5. 安装Python依赖
echo "📚 安装Python依赖..."
pip install \
    openai \
    sentence-transformers \
    faiss-cpu \
    chromadb \
    pytest

# 6. 完成
echo ""
echo "=========================================="
echo "✅ 安装完成！"
echo "=========================================="
echo "运行命令: ./启动软件.sh"
echo "或手动激活: source .venv/bin/activate"
echo "然后运行: python src/cxxcrafter/gui/main.py"
echo "=========================================="