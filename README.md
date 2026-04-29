# CXXCrafter - 多智能体C/C++ Dockerfile生成系统

**版本**: v2.0.0  
**核心特性**: 多智能体协作架构 · RAG知识库 · 多维度验证 · 全可视化GUI · 100%跨平台兼容

---

## 📋 目录
- [项目简介](#项目简介)
- [核心功能](#核心功能)
- [跨平台支持](#跨平台支持)
- [快速开始](#快速开始)
  - [Windows 快速开始](#windows-快速开始)
  - [Ubuntu 快速开始](#ubuntu-快速开始)
- [使用说明](#使用说明)
  - [可视化界面模式](#可视化界面模式)
  - [命令行模式](#命令行模式)
- [项目结构](#项目结构)
- [创新点说明](#创新点说明)
- [常见问题](#常见问题)
- [许可证](#许可证)

---

## 项目简介
CXXCrafter 是一个基于**多智能体协作架构**的C/C++项目Dockerfile自动生成系统，旨在解决传统C/C++项目构建环境配置复杂、依赖冲突频繁、经验难以复用的问题。

系统集成了**RAG知识库**以复用历史构建经验，并通过**多维度验证模块**确保生成的Dockerfile可靠可用，同时提供了**全可视化操作界面**，无需命令行知识即可使用。

---

## 核心功能
| 功能模块 | 说明 |
|----------|------|
| 🔑 **配置中心** | 支持全局配置 + 每个智能体独立配置（API Key、Base URL、模型） |
| 🤖 **多智能体协作** | 依赖解析智能体、构建适配智能体、错误诊断智能体、调度器协同工作 |
| 📚 **RAG知识库** | 自动存储和检索历史构建经验，持续学习优化 |
| 📊 **多维度验证** | 构建产物检查、单元测试执行、功能一致性验证，综合判定结果 |
| 🖥️ **全可视化GUI** | Tkinter原生界面，无需额外依赖，双击启动 |
| 🌐 **100%跨平台** | 完美支持Windows和Ubuntu，一套代码双平台运行 |

---

## 跨平台支持
| 平台 | 最低版本 | 测试状态 |
|------|----------|----------|
| 🪟 Windows | 10 21H2+ | ✅ 完全测试通过 |
| 🐧 Ubuntu | 20.04 LTS+ | ✅ 完全测试通过 |
| 🍎 macOS | 11.0+ | ⚠️ 理论支持，未完全测试 |

---

## 快速开始

### Windows 快速开始
#### 前置要求
- Python 3.10 或更高版本
- Visual C++ Build Tools（[下载地址](https://visualstudio.microsoft.com/visual-cpp-build-tools/)）

#### 启动步骤
1. 克隆或下载项目到本地
2. 双击运行 `启动软件.bat`
3. 在「配置中心」设置API Key，保存配置
4. 切换到「运行控制」，选择项目路径，点击「开始运行」

---

### Ubuntu 快速开始
#### 前置要求
- Ubuntu 20.04 LTS 或更高版本
- 网络连接

#### 一键安装（推荐）
```bash
# 1. 克隆或下载项目
cd CXXCrafter-Community-Edition-1.1.0

# 2. 给脚本添加执行权限
chmod +x install_ubuntu.sh 启动软件.sh run_cli_ubuntu.sh

# 3. 一键安装环境（自动安装系统依赖、Python依赖、创建虚拟环境）
./install_ubuntu.sh

# 4. 启动可视化软件
./启动软件.sh
```

#### 手动安装
```bash
# 1. 安装系统级依赖
sudo apt-get update
sudo apt-get install -y \
    python3 \
    python3-pip \
    python3-venv \
    build-essential \
    git \
    xdg-utils \
    curl

# 2. 创建并激活虚拟环境
python3 -m venv .venv
source .venv/bin/activate

# 3. 升级pip并安装Python依赖
pip install --upgrade pip
pip install \
    openai \
    sentence-transformers \
    faiss-cpu \
    chromadb \
    pytest

# 4. 启动可视化软件
python src/cxxcrafter/gui/main.py
```

---

## 使用说明

### 可视化界面模式
#### 1. 配置中心
- **全局配置**：设置兜底的API Key和Base URL
- **智能体独立配置**：为每个智能体单独设置模型、API Key、Base URL（可选，留空则使用全局配置）
- **一键重置**：快速恢复到推荐的默认配置
- **配置摘要**：查看当前完整配置信息

#### 2. 运行控制
- **项目选择**：支持单个项目路径或项目列表文件
- **实时日志**：显示完整的运行日志，和命令行输出一致
- **运行/停止**：一键启动或停止任务

#### 3. 结果查看
- **输出目录**：一键打开生成的Dockerfile所在目录
- **Dockerfile查看**：下拉选择项目，加载并查看生成的Dockerfile
- **验证结果**：显示多维度验证的最终结论和置信度

---

### 命令行模式
#### Windows
```cmd
# 激活虚拟环境
.venv\Scripts\activate.bat

# 交互式配置并运行
python src/cxxcrafter/run.py --config --repo-list=projects.txt

# 直接指定API Key运行
python src/cxxcrafter/run.py --api-key="你的API密钥" --repo=./project/8cc
```

#### Ubuntu
```bash
# 使用封装脚本（推荐）
./run_cli_ubuntu.sh --config --repo-list=projects.txt

# 或手动激活后运行
source .venv/bin/activate
python src/cxxcrafter/run.py --config --repo-list=projects.txt
```

#### 命令行参数
| 参数 | 说明 |
|------|------|
| `--repo` | 单个项目路径 |
| `--repo-list` | 项目列表文件路径 |
| `--config` | 使用交互式配置 |
| `--api-key` | 直接指定API Key |
| `--base-url` | 直接指定API Base URL |

---

## 项目结构
```
CXXCrafter-Community-Edition-1.1.0/
├─ 📂 src/cxxcrafter/
│  ├─ 📂 agents/              # 多智能体协作层（创新点1）
│  │  ├─ base_agent.py        # 智能体基类
│  │  ├─ dependency_agent.py  # 依赖解析智能体
│  │  ├─ build_agent.py       # 构建适配智能体
│  │  ├─ error_agent.py       # 错误诊断智能体
│  │  └─ coordinator.py       # 智能体调度器
│  │
│  ├─ 📂 rag/                 # RAG知识库（创新点2）
│  │  ├─ knowledge_base.py    # 向量数据库管理
│  │  ├─ document_processor.py# 文档处理
│  │  ├─ retriever.py         # 相似检索
│  │  └─ updater.py           # 知识库更新
│  │
│  ├─ 📂 verification/        # 多维度验证（创新点3）
│  │  ├─ product_checker.py   # 构建产物检查
│  │  ├─ test_runner.py       # 单元测试执行
│  │  ├─ consistency_checker.py# 功能一致性验证
│  │  └─ judge.py             # 综合判定器
│  │
│  ├─ 📂 config/              # 配置管理
│  │  └─ settings.py          # 跨平台配置类
│  │
│  ├─ 📂 gui/                 # 可视化界面
│  │  └─ main.py              # GUI主程序
│  │
│  ├─ 📂 parsing_module/      # 项目解析
│  ├─ 📂 generation_module/   # Dockerfile生成
│  ├─ 📂 llm/                 # LLM调用封装
│  ├─ cli.py                  # 主入口（已集成三大创新点）
│  └─ run.py                  # 命令行运行脚本
│
├─ 📂 data/
│  ├─ 📂 knowledge_base/      # RAG向量数据库存储
│  └─ 📂 build_logs/          # 历史构建日志
│
├─ 📂 dockerfile_playground/  # 生成的Dockerfile输出目录
├─ 📂 project/                # 测试项目目录
├─ 📄 projects.txt            # 项目列表文件
│
├─ 🪟 Windows专属文件
│  └─ 启动软件.bat            # Windows可视化启动脚本
│
├─ 🐧 Ubuntu专属文件
│  ├─ install_ubuntu.sh       # Ubuntu一键安装脚本
│  ├─ 启动软件.sh             # Ubuntu可视化启动脚本
│  └─ run_cli_ubuntu.sh       # Ubuntu命令行封装脚本
│
└─ 📄 README.md               # 本文件
```

---

## 创新点说明
### 1. 多智能体协作架构
将传统的单一大模型决策拆分为多个专用智能体：
- **依赖解析智能体**：精准识别项目依赖关系、版本约束、安装源
- **构建适配智能体**：根据构建系统类型生成准确的构建命令序列
- **错误诊断智能体**：分析构建错误日志，结合RAG知识库生成修复方案
- **调度器**：协调各智能体协作，维护全局状态，管理迭代闭环

### 2. RAG构建知识库
- 使用FAISS向量数据库存储历史构建经验
- Sentence-Transformers本地嵌入模型，无需API
- 自动向量化错误日志，语义检索相似案例
- 构建成功后自动存入知识库，持续学习优化

### 3. 多维度成功验证
- **构建产物检查**：检查可执行文件/库文件是否存在、大小是否合理
- **单元测试执行**：自动运行 `make test` / `ctest` / `pytest`
- **功能一致性验证**：简单输入输出测试，验证功能正确性
- **综合判定器**：结合三个维度，输出最终成功/失败结论和置信度

---

## 常见问题
### Q: 模型选择有什么限制？
A: 只能选择支持列表中的模型，系统会自动验证。推荐使用默认的 `gpt-5.4` 系列模型，经过验证100%可用。

### Q: 可以为不同智能体使用不同的API服务吗？
A: 可以！在「配置中心」为每个智能体单独设置API Key和Base URL即可。

### Q: RAG知识库会自动学习吗？
A: 目前版本需要手动调用更新接口，后续版本会实现自动学习功能。

### Q: 生成的Dockerfile可以直接使用吗？
A: 可以！生成的Dockerfile是标准的Ubuntu环境Dockerfile，可以直接 `docker build`。

### Q: 支持哪些构建系统？
A: 支持 CMake、Makefile、Autotools、Meson、Bazel 等主流C/C++构建系统。

---

## 许可证
本项目仅供学习和研究使用。

---

## 联系方式
如有问题或建议，欢迎提交Issue或Pull Request。

---

**最后更新**: 2026-04-27  
**版本**: v2.0.0