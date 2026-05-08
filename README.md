# CXXCrafter - 多智能体 C/C++ Dockerfile 生成与验证系统

**版本**：v2.1.0  
**定位**：面向 C/C++ 开源项目的 Dockerfile 自动生成、构建修复与多维度验证平台  
**核心能力**：多智能体协作 · RAG 知识增强 · Docker 构建/验证闭环 · GUI / Headless 双入口

---

## 目录

- [项目简介](#项目简介)
- [核心功能](#核心功能)
- [系统架构](#系统架构)
- [环境要求](#环境要求)
- [快速开始](#快速开始)
  - [Windows](#windows)
  - [Ubuntu / Linux](#ubuntu--linux)
- [使用方式](#使用方式)
  - [图形界面模式](#图形界面模式)
  - [Headless 批处理模式](#headless-批处理模式)
  - [命令行参数](#命令行参数)
- [项目结构](#项目结构)
- [配置说明](#配置说明)
- [常见问题](#常见问题)
- [已知限制](#已知限制)
- [许可证](#许可证)

---

## 项目简介

CXXCrafter 是一个面向 C/C++ 开源项目的自动化 Dockerfile 生成与构建验证系统。

它的目标不是简单地“生成一个能跑的 Dockerfile”，而是尽可能在**依赖识别、构建命令生成、错误诊断、自动修复、结果验证**等环节形成完整闭环，从而提升复杂项目在容器环境中的可构建性与可复现性。

当前版本重点强化了以下能力：

- 使用**多智能体协作**处理依赖分析、构建规划、错误诊断与修复建议；
- 使用 **RAG** 复用历史构建失败与成功案例；
- 使用 **多维度验证** 判断 Dockerfile 产物是否真正可用；
- 支持 **GUI 可视化操作** 与 **Headless 批量处理** 两种使用模式；
- 支持多种常见构建系统，包括 **CMake、Make、Autotools、Meson** 等。

---

## 核心功能

| 功能模块 | 说明 |
|---|---|
| 多智能体协作 | 将依赖解析、构建规划、错误诊断、Dockerfile 修复拆分为不同智能体协作完成 |
| RAG 知识库 | 记录历史失败/成功案例，在相似错误出现时提供参考经验 |
| Dockerfile 生成 | 自动生成适用于 Ubuntu 基础镜像的构建 Dockerfile |
| 构建修复闭环 | 构建失败后自动分析日志并尝试修复 |
| 多维度验证 | 静态一致性检查、构建产物检查、动态测试执行 |
| GUI 界面 | 基于 Tkinter 的可视化操作界面 |
| Headless 模式 | 适合批量项目处理、自动化脚本和 CI 环境 |
| 缓存机制 | 对 LLM 调用进行缓存，减少重复请求 |
| 失败日志汇总 | 支持统一记录批量失败项目的摘要信息 |

---

## 系统架构

CXXCrafter 当前主要由以下模块组成：

1. **Agent 层**
   - Dependency Agent：识别依赖、系统包、Python 包、特征标签
   - Build Agent：生成构建计划、测试计划、运行命令
   - Error Agent：分析构建失败日志并给出修复建议
   - Dockerfile Repair Agent：基于错误分析生成最小修复补丁

2. **Coordinator 层**
   - 负责组织各 Agent 的调用顺序
   - 管理构建、修复、验证的闭环流程
   - 记录执行轨迹、RAG 命中情况和最终摘要

3. **Generation 层**
   - 负责把分析结果渲染为最终 Dockerfile
   - 处理路径、工作目录、基础依赖、构建命令与测试命令

4. **Execution / Verification 层**
   - 执行 Docker build
   - 进行构建产物验证
   - 运行 smoke test / test suite
   - 输出最终裁决

5. **RAG 层**
   - 存储构建失败与成功案例
   - 检索相似错误与修复方案
   - 支持持续积累经验

---

## 环境要求

### 基础要求

- Python 3.10 或更高版本
- Docker Desktop / Docker Engine
- Git
- 可访问 OpenAI-compatible API 的模型服务

### 推荐环境

- Windows 10 / 11
- Ubuntu 22.04 LTS
- 足够的磁盘空间用于 Docker 镜像与构建缓存

### Python 依赖

项目实际运行会用到的核心依赖包括：

- `openai`
- `numpy`
- `faiss-cpu`（可选，但建议安装）
- `sentence-transformers`（可选，但建议安装）
- `tkinter`（通常随 Python 自带）
- 以及项目自身的其他运行依赖

如果你使用项目内的 requirements 文件，请优先按仓库中的 requirements 安装。

---

## 快速开始

### Windows

#### 方式一：使用 GUI 启动脚本

1. 双击运行 `启动软件.bat`
2. 在「配置」页填写 API Key、Base URL 和模型
3. 在「运行」页选择单个项目目录或项目列表文件
4. 点击「开始运行」

#### 方式二：命令行启动 GUI

如果你希望手动启动，可使用项目入口脚本：

```bat
python src\cxxcrafter\gui\main.py
```

---

### Ubuntu / Linux

#### 安装系统依赖

```bash
sudo apt-get update
sudo apt-get install -y \
    python3 \
    python3-pip \
    python3-venv \
    build-essential \
    git \
    curl \
    xdg-utils
```

#### 创建虚拟环境并安装 Python 依赖

```bash
python3 -m venv .venv
source .venv/bin/activate

pip install --upgrade pip
pip install openai numpy sentence-transformers faiss-cpu
```

#### 启动 GUI

```bash
./启动软件.sh
```

或直接运行：

```bash
python src/cxxcrafter/gui/main.py
```

---

## 使用方式

### 图形界面模式

GUI 提供四个主要页面：

#### 1. 配置页
- 设置全局 API Key、Base URL、模型
- 为各个智能体单独配置模型/Key/URL
- 设置输出目录、日志目录、默认基础镜像
- 开启/关闭构建、验证、缓存、RAG、BuildKit 等功能

#### 2. 运行页
- 选择单个项目目录
- 或选择项目列表文件进行批量处理
- 查看实时日志
- 支持停止任务、清空日志、设置超时参数

#### 3. 结果页
- 浏览输出目录
- 查看生成的 Dockerfile
- 快速定位每个项目的输出结果

#### 4. 关于页
- 查看版本与功能说明

---

### Headless 批处理模式

如果你希望在不打开 GUI 的情况下运行，可以使用 Headless 模式。

#### 单项目

```bash
python src/cxxcrafter/gui/main.py --headless --project <项目路径>
```

#### 批量项目

```bash
python src/cxxcrafter/gui/main.py --headless --repo-list <项目列表文件>
```

其中项目列表文件每行一个项目路径。

---

### 命令行参数

| 参数 | 说明 |
|---|---|
| `--headless` | 不启动 GUI，直接执行单项目或批处理 |
| `--project` | 单个项目路径 |
| `--repo-list` | 项目列表文件，每行一个项目路径 |
| `--output-dir` | Dockerfile 输出目录 |
| `--log-dir` | 构建日志输出目录 |
| `--api-key` | 全局 API Key |
| `--base-url` | 全局 Base URL |
| `--model-name` | 全局模型名称 |
| `--enable-build` / `--disable-build` | 开启/关闭构建 |
| `--enable-verification` / `--disable-verification` | 开启/关闭验证 |
| `--generate-only` | 只生成，不构建、不验证 |
| `--use-cache` / `--no-cache` | 开启/关闭 LLM 缓存 |
| `--use-buildkit` / `--no-buildkit` | 开启/关闭 BuildKit |
| `--buildkit-progress` | BuildKit 输出模式 |
| `--default-base-image` | 默认基础镜像 |
| `--max-repair-rounds` | 最大修复轮次 |
| `--build-timeout-seconds` | 构建超时 |
| `--verify-timeout-seconds` | 验证超时 |
| `--project-timeout-seconds` | 项目总超时 |
| `--stop-on-docker-error` / `--no-stop-on-docker-error` | 是否遇到 Docker 异常立即停止 |
| `--max-consecutive-failures` | 批处理连续失败阈值 |
| `--batch-summary-path` | 批处理摘要保存路径 |

---

## 项目结构

```text
CXXCrafter-Community-Edition-1.1.0/
├─ src/
│  └─ cxxcrafter/
│     ├─ agents/
│     │  ├─ base_agent.py
│     │  ├─ dependency_agent.py
│     │  ├─ build_agent.py
│     │  ├─ error_agent.py
│     │  ├─ dockerfile_repair_agent.py
│     │  └─ coordinator.py
│     ├─ generation_module/
│     │  └─ dockerfile_generator.py
│     ├─ execution/
│     │  ├─ executor.py
│     │  └─ batch_executor.py
│     ├─ rag/
│     │  ├─ rag_service.py
│     │  ├─ knowledge_base.py
│     │  ├─ retriever.py
│     │  ├─ updater.py
│     │  └─ document_processor.py
│     ├─ verification/
│     │  └─ judge.py
│     ├─ runtime/
│     │  └─ os_compat.py
│     ├─ cache.py
│     ├─ cli.py
│     └─ gui/
│        └─ main.py
├─ data/
│  ├─ knowledge_base/
│  └─ build_logs/
├─ dockerfile_playground/
├─ 启动软件.bat
├─ 启动软件.sh
├─ install_ubuntu.sh
├─ run_cli_ubuntu.sh
└─ README.md
```

---

## 配置说明

### 环境变量

项目会自动读取常见环境变量作为默认配置：

- `OPENAI_API_KEY`
- `OPENAI_BASE_URL`
- `OPENAI_MODEL`
- `DEEPSEEK_API_KEY`
- `ANTHROPIC_API_KEY`
- `BASE_URL`
- `MODEL_NAME`

如果 GUI 中没有单独填写某个智能体的配置，则会回退到全局配置；全局配置也为空时，会继续尝试环境变量。

### 说明

- 默认模型和接口可在 GUI 中覆盖
- 某些 OpenAI-compatible 服务只要接口兼容即可使用
- 如果模型不支持当前 endpoint，系统会尝试自动切换兼容模型

---

## 常见问题

### 1. Docker 构建失败，提示 Docker daemon 不可用
请检查：

- Docker Desktop 是否启动
- Docker 引擎是否正常
- 当前用户是否有 Docker 访问权限
- Windows 下是否正确启用了 WSL2 后端

### 2. 构建依赖缺失
如果日志里出现：

- `sphinx-build: not found`
- `meson: command not found`
- `ninja: not found`
- `cmake: command not found`
- `bad interpreter`
- `undefined reference`

通常表示项目依赖识别或构建命令需要补充。系统会尽量自动修复，但复杂项目仍可能需要人工微调。

### 3. 为什么有些项目只生成了 Dockerfile，没有验证通过？
可能原因包括：

- 项目本身不适合在容器中直接运行
- 该项目是库、工具或文档项目，没有明确 runtime 命令
- 验证阶段被自动跳过
- 构建成功，但 smoke test 或测试套件失败

### 4. RAG 知识库会自动增长吗？
会。系统会在成功构建或修复后记录经验，用于后续相似问题检索。

### 5. 可以给不同智能体设置不同的模型吗？
可以。GUI 的「多智能体配置」支持为 dependency / build / error / repair 四个智能体分别设置独立模型、API Key 和 Base URL。

---

## 已知限制

- 对极其复杂的 C/C++ 超大仓库，仍可能需要人工干预
- 某些项目的自定义构建脚本无法完全通过规则推断
- 依赖识别依赖项目文档、日志与历史案例，不能保证一次性完全准确
- macOS 未作为主要目标平台进行充分验证
- Docker 环境不可用时，构建与验证无法执行

---

## 开发说明

### 代码风格

项目采用“结构化输出 + 规则化补丁 + 可追踪日志”的方式实现：

- Agent 负责分析与建议
- Coordinator 负责流程控制
- Generator 负责稳定渲染
- Verification 负责结果裁决
- RAG 负责经验复用

### 调试建议

如果你在开发或排障时想查看更详细信息，可以关注以下日志：

- `data/build_logs/*_build.log`
- `data/build_logs/*_verify.log`
- `data/build_logs/*_summary.json`
- `data/knowledge_base/`

---

## 许可证

本项目仅供学习、研究与内部测试使用。  
如需商用或分发，请根据你的实际许可证条款处理。

---

## 联系方式

如有问题或建议，欢迎提交 Issue 或 Pull Request。

---

**最后更新**：2026-05-08  
**版本**：v2.1.0