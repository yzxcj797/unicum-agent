<div align="center">

# 🚀 Unicum-Agent

一个通用agent，mini版claude code，非常快以及非常省(token)！

[![Python 3.10+](https://img.shields.io/badge/Python-3.10+-3776AB?logo=python&logoColor=white)](https://python.org)
[![License](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

 [快速开始](#-快速开始) · [架构](#-架构) · [上下文管理](#-上下文管理) · [记忆管理](#-记忆管理)

</div>

---

## ✨ 特性

<table>
<tr>
<td width="50%">

### ⚡ 极致 Token 效率

轻量任务同等质量下，token消耗相比claude code降低约 **90%**

- 结构化事实提取替代机械截断
- 三阶段感知压缩动态调整保护边界
- L0/L1/L2 渐进式上下文架构

</td>
<td width="50%">

### 🧠 零成本记忆积累

跨会话知识自动积累，无需额外 LLM 调用

- 纯规则从工具调用中推导记忆
- JSON 结构化存储，项目级隔离
- 会话启动自动注入系统提示词

</td>
</tr>
<tr>
<td width="50%">

### 📐 智能上下文管理

三层架构确保长任务中始终精准上下文

- L0 全局事实汇总始终对 LLM 可见
- L1 压缩调用链，信息密度极高
- L2 完整输出按需检索，不浪费 Token

</td>
<td width="50%">

### ⚡ 运行速度飞快

极简架构，单次循环低延迟响应

- 单进程运行，无额外服务依赖
- 流式输出，实时看到 Agent 思考过程
- 轻量核心，启动即用无需等待

</td>
</tr>
</table>

---

## 🚀 快速开始

### 安装

```bash
git clone https://github.com/yzxcj797/unicum-agent.git
cd unicum-agent
pip install -r requirements.txt
```

### 配置

```bash
# 复制环境变量模板
cp .env.example .env  # linux
copy .env.example .env  # windows

# 编辑 .env，填入 API Key（建议zhipu，所有测试及优化均在zhipu下进行）
# ZHIPUAI_API_KEY=xxx
# OPENAI_API_KEY=xxx
# ANTHROPIC_API_KEY=xxx
# OPENROUTER_API_KEY=xxx
```

### 运行

```bash
# 直接运行
python cli.py  #不建议此种方式，不带路径的话默认是此项目根目录，推荐计时模式

# 指定工作目录
python cli.py --dir /your-project

# 调试模式（打印 API 请求/响应）
python cli.py --dir /your-project --debug  #需要查看完整prompt时可打开（包括request和response）

# 计时模式（显示每个节点耗时）
python cli.py --dir /your-project --time  #强烈推荐！！！

# 示例
python cli.py --dir /your-project --debug --time
```

### CLI 命令

| 命令 | 说明 |
|------|------|
| `/help` | 显示帮助 |
| `/new` | 新建会话（刷新记忆） |
| `/cd <dir>` | 切换工作目录（自动切换项目记忆） |
| `/tools` | 查看工具状态 |
| `/usage` | 查看当前会话使用统计 |
| `/quit` | 退出 |

---

## 🏗 架构

```
unicum-agent/
├── cli.py                          # CLI 入口（Rich + prompt_toolkit）
├── core/
│   ├── agent.py                    # Agent 核心循环
│   ├── model_client.py             # 统一 LLM 客户端 + 上下文压缩
│   ├── prompt_builder.py           # 系统提示词构建
│   ├── fact_extractor.py           # 结构化事实提取
│   ├── fact_to_memory.py           # 自动记忆提取
│   ├── context_archive.py          # L2 归档存储
│   └── memory_store.py             # 结构化记忆存储
├── tools/
│   ├── registry.py                 # 工具注册中心（AST 自动发现）
│   ├── file_tools.py               # read_file / write_file / search_files
│   ├── terminal_tool.py            # terminal
│   ├── web_search_tool.py          # web_search / web_read
│   ├── clarify_tool.py             # clarify
│   ├── recall_tool.py              # recall
│   └── memory_tool.py              # memory
├── my_agent/
│   ├── constants.py                # 全局常量 + TaskPhase 枚举
│   ├── config.py                   # 多提供商配置
│   └── logging.py                  # 日志配置
└── tests/                          # 106 个单元测试
```

### 依赖链

```
my_agent/constants.py    ← 零依赖
       ↓
tools/registry.py        ← 零依赖
tools/*.py               ← 仅 import registry
       ↓
core/fact_extractor.py   ← 零依赖
core/fact_to_memory.py   ← 零依赖
core/context_archive.py  ← 零依赖
core/memory_store.py     ← 仅 constants
core/model_client.py     ← config + constants
core/prompt_builder.py   ← 仅 constants
       ↓
core/agent.py            ← import tools + core/*
       ↓
cli.py                   ← import core + my_agent
```

---

## 📊 上下文管理

Unicum-Agent 的上下文管理实现了三层创新，让 Agent 在长任务中始终保持精准的上下文。

### 1. 结构化事实提取

每次工具调用后，纯规则提取一行结构化摘要，替代传统的机械截断：

```
read_file(auth.py, 120行)     → [read_file] auth.py (120行, Python)
write_file(auth.py, 写入3KB)  → [write_file] auth.py (写入 3KB)
terminal(npm test, 成功)      → [terminal] npm test → 退出码0, 47行输出
search_files("auth", 5处)     → [search_files] "auth" in src → 5处匹配
```

支持 7 种工具类型，零 LLM 调用，提取结果直接用于上下文压缩。

### 2. 任务阶段感知压缩

定义三阶段状态机，不同阶段采用不同的保护策略：

```
PLANNING ──→ EXECUTING ──→ VERIFYING
(读/搜索)    (写文件)      (测试/验证)
```

| 阶段 | 保护前 N 条 | 保护后 N 条 | 特殊行为 |
|------|:-----------:|:-----------:|----------|
| PLANNING | 1 | 4 | 无 |
| EXECUTING | 2 | 6 | 保护用户任务 + 计划 |
| VERIFYING | 1 | 4 | 保留 write_file / terminal 事实 |

压缩时用事实替换完整工具输出：`120行代码 → [已归档] auth.py (120行, Python)`

### 3. L0/L1/L2 渐进式上下文

```
┌─────────────────────────────────────────────────┐
│  L0 · 始终可见的全局事实汇总 (~200 token)          │
│  [会话进度]                                      │
│  - [read_file] auth.py (120行, Python)           │
│  - [terminal] npm test → 退出码0                 │
│  - [write_file] auth.py (写入 3KB)               │
├─────────────────────────────────────────────────┤
│  L1 · 压缩后的工具调用链                          │
│  tc_1: [已归档] [read_file] auth.py (120行)      │
│  tc_2: [已归档] [terminal] npm test → 退出码0     │
│  tc_3: [write_file] auth.py 完整结果 (保留)       │
├─────────────────────────────────────────────────┤
│  L2 · 完整工具输出（内存归档）                     │
│  → recall("auth.py") 即可检索完整内容              │
└─────────────────────────────────────────────────┘
```

- **L0**：插入消息历史第一条，始终对 LLM 可见，多次压缩时更新而非重复插入
- **L1**：压缩后的调用链，保留结构化事实标记，信息密度极高
- **L2**：完整输出存入内存归档，可通过 `recall` 工具按需检索

---

## 🧠 记忆管理

Unicum-Agent 的记忆管理实现了从"每次从零开始"到"跨会话知识积累"的飞跃，且全程零额外 LLM 调用。

### 整体流程

```
    会话 #1                          会话 #2
┌──────────────────┐          ┌──────────────────┐
│  工具调用          │          │  启动时加载记忆    │
│  read_file auth.py │          │  system prompt:   │
│  terminal npm test │          │  "已知文件:        │
│  write_file auth.py│          │   auth.py: Python  │
│         ↓          │          │   项目使用 npm"    │
│  extract_fact()    │          │         ↓          │
│         ↓          │          │  Agent 已知道      │
│  会话结束           │          │  项目结构和技术栈   │
│  _auto_extract_    │          └──────────────────┘
│   memories()       │
│  (纯规则，零成本)   │
│         ↓          │
│  memory_store      │
│  .add() → JSON     │
└──────────────────┘
         ↕ 持久化
~/.unicum-agent/memory/
  ├── global.json
  └── projects/
      └── auth-service/
          └── memory.json
```

### 1. 结构化记忆存储

JSON 格式存储，双层层作用域，原子写入保证数据安全：

```
~/.unicum-agent/memory/
├── global.json                     ← 用户档案、环境（跨项目）
└── projects/
    ├── auth-service/memory.json    ← 项目级记忆
    └── data-pipeline/memory.json   ← 按工作目录自动隔离
```

```jsonc
// global.json
{
  "user": {
    "role": "全栈工程师",
    "preferences": ["喜欢 TypeScript", "2空格缩进"]
  }
}

// projects/auth-service/memory.json
{
  "project": {
    "files": {
      "auth.py": "Python, 已修改",
      "config.py": "JSON, 28行"
    },
    "commands": {
      "npm": "项目使用 npm (npm test 成功)"
    }
  }
}
```

### 2. 自动记忆提取

会话结束时，纯规则从已有的结构化事实中推导出跨会话记忆：

| 工具 | Fact | 推导出的记忆 |
|------|------|-------------|
| `read_file` | `[read_file] auth.py (120行, Python)` | `files.auth.py = "Python, 120行"` |
| `write_file` | `[write_file] auth.py (写入 3KB)` | `files.auth.py = "Python, 已修改"` |
| `terminal` | `[terminal] npm test → 退出码0, 47行输出` | `commands.npm = "项目使用 npm"` |

**智能过滤**避免记忆污染：

- ✅ 成功操作 + 有意义路径 + 有意义命令 → 提取
- ❌ FAIL 结果 / `node_modules/` / 小文件 / 失败命令 / 未知工具 → 跳过
- ❌ `search_files` / `web_*` / `clarify` → 跳过（噪音太多）
- 同一 key 后者覆盖前者（write 覆盖 read）

### 3. 会话启动注入

每次启动或 reset 时，记忆自动注入系统提示词：

```
## 跨会话记忆

以下是之前会话中积累的知识，请主动利用：

项目 [auth-service]:
已知文件:
  auth.py: Python, 已修改
  config.py: JSON, 28行
  package.json: JSON, 28行

如果发现记忆中的信息过时或错误，可用 memory 工具更新或删除。
```

### 对比

| 维度 | Claude Code | OpenHands | **Unicum-Agent** |
|------|-------------|-----------|:------------:|
| 上下文管理 | 静态保护边界 | 滑动窗口 | **三阶段感知 + L0/L1/L2** |
| 记忆提取 | LLM 后台审查 | LLM 后台审查 | **纯规则，零 token** |
| 存储格式 | Markdown 文本 | JSON | **JSON + 项目隔离** |
| Token 效率 | ~300K | ~200K | **~30K** |
| 记忆积累成本 | 每次审查消耗 token | 每次审查消耗 token | **零成本** |

---

## 📦 支持的 LLM 提供商 

| 提供商 | 环境变量 | 默认模型 | API 格式 |
|--------|----------|----------|----------|
| **智谱** | `ZHIPUAI_API_KEY` | glm-5.1 | Anthropic 原生 |
| **OpenAI** | `OPENAI_API_KEY` | gpt-4o | OpenAI 兼容 |
| **Anthropic** | `ANTHROPIC_API_KEY` | claude-sonnet-4-20250514 | Anthropic 原生 |
| **OpenRouter** | `OPENROUTER_API_KEY` | claude-sonnet-4-20250514 | OpenAI 兼容 |

建议使用智谱Anthropic接口,所有测试及优化均在此条件下进行。

---

## 🧪 测试

```bash
# 运行全部 106 个测试
python -m pytest tests/ -v

# 运行特定模块
python -m pytest tests/test_fact_to_memory.py -v
python -m pytest tests/test_phase_aware.py -v
python -m pytest tests/test_memory_store.py -v
```

| 测试文件 | 覆盖模块 | 测试数 |
|----------|---------|:------:|
| `test_agent.py` | Agent 循环 + 记忆注入 | 8 |
| `test_registry.py` | 工具注册/调度 | 8 |
| `test_fact_extractor.py` | 结构化事实提取 | 12 |
| `test_phase_aware.py` | 阶段感知压缩 | 13 |
| `test_progressive_context.py` | L0/L2 归档/recall | 15 |
| `test_memory_store.py` | 记忆存储 + memory 工具 | 18 |
| `test_fact_to_memory.py` | 自动记忆提取 | 26 |

---




## 📜 License

MIT License

---

<div align="center">

**如果觉得有用，给个 ⭐ Star 吧！**

</div>
