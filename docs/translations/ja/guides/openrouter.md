---
description: OpenRouterをVOOLに接続し、一つのAPIキーで多くのクラウドAIモデルを使う方法。
translation: machine
source_sha256: 3a09fe6c636e2b8dd14a2d627c4653055dbb10bf8fd8221b7a3bcccf3044cb19
---

# OpenRouterを接続する

OpenRouterは、一つのキーで多くのプロバイダにアクセスできるサービスです。VOOLはOpenRouterアカウントの接続に対応しています。

## 接続する

OpenRouterのアカウントでキーを作成し、**設定 → APIキー** に貼り付けて **キーを保存** を押します。VOOLはOpenRouterでキーを一度確認してから、このコンピュータ上の暗号化されたファイルに保存します（[セキュリティモデル](../trust/security.md#キー)を参照）。

## 請求と利用の帰属

{% hint style="info" %}
OpenRouterのアカウントはご自身のものです。VOOLは、ご自身のキーを使って、このコンピュータから直接リクエストを送ります。Parad0xがプロンプトを中継したり、利用料を支払ったりすることはありません。VOOLの実際の利用は、OpenRouter上のVOOLの公開集計統計に表れることがありますが、個々のプロンプトや個人を特定する情報は公開されません。
{% endhint %}

## 接続を解除する

**設定 → APIキー** でキーを削除します。サーバー側でもキーを無効にしたい場合は、OpenRouterのアカウントでもキーを取り消してください。
