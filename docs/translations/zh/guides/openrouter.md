---
description: 如何把 OpenRouter 连接到 VOOL，用一个 API 密钥访问众多云端 AI 模型。
translation: machine
source_sha256: 75cb20d43eb37adb1d30913959ca1cb46864ad57ba7a1a22ec261f0e0eaf2e9a
---

# 连接 OpenRouter

OpenRouter 通过一个密钥提供对众多提供方的访问。VOOL 支持添加 OpenRouter 密钥。

## 添加密钥

在你的 OpenRouter 账户中创建一个密钥，把它粘贴到 **设置 → API 密钥** 下，然后按 **保存密钥**。VOOL 会向 OpenRouter 验证一次这个密钥，然后把它保存在这台电脑上的一个加密文件中（见[安全模型](../trust/security.md#密钥)）。

## 计费与归属

{% hint style="info" %}
你的 OpenRouter 账户仍然属于你。VOOL 使用你的密钥，直接从这台电脑发送请求。Parad0x 不会转发你的提示词，也不会替你支付用量。真实的 VOOL 用量可能会出现在 VOOL 公开的 OpenRouter 汇总统计中，但不会公开单个提示词或身份信息。
{% endhint %}

## 移除密钥

在 **设置 → API 密钥** 下移除该密钥。如果你希望它在服务器端也彻底失效，请同时在你的 OpenRouter 账户中撤销该密钥。
