---
description: VOOL 是如何构建的——一页看懂架构、开发者设置，以及如何贡献。
translation: machine
source_sha256: 7cb91029e16c6510821e1ca26b509da8cdfb63113111619d3a31e364a2f088d3
---

# 架构与贡献

## 一页看懂架构

VOOL 是一个本地优先的桌面运行时，带有一个提供服务的核心：

```text
┌──────────────────────────────────────────────┐
│ Native window / browser console              │
│  (served pages; the console you use)         │
├──────────────────────────────────────────────┤
│ Served runtime (local HTTP API)              │
│  routing → tool execution → answers          │
├───────────────┬──────────────┬───────────────┤
│ Local models  │ Cloud lanes  │ Wallet (opt)  │
│ (Ollama etc.) │ (your keys)  │ pilot lanes   │
├───────────────┴──────────────┴───────────────┤
│ Records: receipts, ledgers, fault registry   │
│ Local storage (SQLite under your profile)    │
└──────────────────────────────────────────────┘
```

代码强制执行的原则：

* **默认本地；云端用你的密钥。** 云端通道只在有你的凭据和你设定的上限时才存在。
* **每项决定只有一个权威。** 权限、支出和网络出口各自只有一个负责的契约；故障在负责它们的边界上进行归类。
* **说清楚什么失败了。** 未知的结果被归类为未知；拒绝会说明什么没有发生；固定的模型绝不会被悄悄替换。
* **一切都有记录。** 工具调用、批准、付款和更新都会在你的用户资料目录下写入回执。

## 开发者设置

```bash
git clone https://github.com/Parad0x-Labs/vool.git
cd vool
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pytest tests -q            # the suite
```

平台说明、完整的工具链布局和发布关卡都在仓库的工程文档中（仓库根目录下的 `docs/`；深层的内部细节不属于这个公开文档站点）。

## 文档：唯一的权威来源

这些文档由 **一个** 来源生成：本仓库的 `docs/` 文件夹（GitBook 布局，`.gitbook.yaml` 位于仓库根目录）。公开文档站点就是从它构建的。要编辑文档：

```bash
python3 tools/docs/build_docs.py --src . --out docs-build --base /docs
python3 tools/docs/check_docs_build.py docs-build /docs
python3 -m http.server -d docs-build 8000   # local preview
```

错误参考（`ERROR_BOOK.md`）和本地化交接清单都是 **从运行时自己的注册表生成的**——请重新生成它们，绝不要手动编辑：

```bash
python -m tools.generate_error_book
python -m tools.generate_localization_handoff
```

如果生成的文件与其注册表不一致，一项专门的测试会让构建失败。

## 贡献

* 小而经过审查的改动；文档中的每一项说法都必须与实际发布的代码一致。
* 保留稳定的代码和标识符——它们比构建版本存在得更久。
* 绝不悄悄扩大权限：新的权限、支出路径或出口通道是设计变更，而不是补丁。
* 测试版的限制要写进文档，而不是隐藏起来——如果你发现了一个没有写进文档的限制，那就是文档中的一个错误。
