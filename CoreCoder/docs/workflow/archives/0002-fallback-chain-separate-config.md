---
title: 主模型和备用模型由 Config 统一读取环境变量
status: archived
depends_on: [0002-fallback-chain-and-budget]
implemented_in: working-tree
archived_on: 2026-08-09
---

# 归档记录

- **状态**：已归档。
- **实现范围**：主模型、备用模型和硬预算由 `Config.from_env()` 统一解析。
- **实现位置**：`corecoder/config.py`、`corecoder/fallback.py` 与 `corecoder/cli.py`。
- **验证范围**：`tests/test_fallback.py` 中的配置、链路和预算测试。

# 决策

主模型、备用模型和硬预算统一由 `Config.from_env()` 读取。`Config` 将主模型连接信息、
预算上限和有序的 `ModelConfig` 备用级别解析为一个配置对象；CLI 以此创建主模型、
`BudgetState` 与 `ModelChain`。

备用级别使用连续编号且从 1 开始的环境变量：
`CORECODER_FALLBACK_<N>_{MODEL,PROVIDER,API_KEY,BASE_URL}`。每一级的 `API_KEY`
直接保存该级别密钥；索引缺失、模型缺失或密钥缺失均视为无效配置。主模型密钥仅使用
`CORECODER_API_KEY` 或 `OPENAI_API_KEY`。

`fallback.py` 不读取环境变量、JSON 或其他配置文件。它只接收解析后的备用级别和显式
创建的 `BudgetState`，负责请求路由、熔断状态、按模型汇总的估算成本和硬预算检查。
因此，不再支持 `corecoder.fallback.json`、间接密钥变量名或独立的预算配置读取路径。

# 后果

- 配置入口唯一，`.env` 与进程环境变量的优先级由 `Config` 集中管理。
- 备用模型的连接信息可读且顺序明确，同时密钥不会被复制到 JSON 配置文件。
- 直接构造 `ModelChain` 的调用方必须显式传入预算状态；未传入时使用无限制预算。
- 该配置决策已完成实现并归档。
