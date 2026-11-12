---
description: 最常见问题的解决方法。
translation: machine
source_sha256: 792c90fd68caf7409212d3742109c46e49d1f7e640aa3e9f70db9fe61ca55d77
---

# 故障排除

## macOS 拒绝打开应用

这是预期的：应用尚未经过公证。请先核对校验和，然后打开 **系统设置 → 隐私与安全**，
点按 **仍要打开**。在 macOS 14 上，你也可以按住 Control 键点按 VOOL，然后选取 **打开**。

## Windows

本次发布没有 Windows 版下载。Windows 需要从源码安装，且为实验性支持；见
[安装指南](../getting-started/install.md#从源码安装)。

## 没有列出任何本地模型

1. 确认运行时正在运行。
2. 确认至少已拉取一个模型。
3. 确认端口与 VOOL 预期的一致。

```bash
curl http://localhost:11434/api/tags
```

如果这条命令没有任何返回，说明运行时没有启动。

## 回答很慢

通常是模型大小相对可用内存来说太大了。见
[运行本地模型](../../../guides/local-models.md)。

## 从头开始

退出 VOOL，并删除[配置](../../../reference/configuration.md)中列出的配置目录。工作区中的文件不会受影响。
