# 登録済みAPIキーの検証

AWSにデプロイしたサンプルで、同じCognitoテストユーザー `owner`、同じ登録済みSecret、同じGateway経路を使い、登録値を入れ替えて検証した。Codexはモック固有の説明を含むツール一覧を取得した後、`mock___whoami` を `testRegisteredKey` 付きで呼び出した。

| 登録した値 | モックMCPの結果 | Codexで確認した応答 |
| --- | --- | --- |
| 無効なテスト値 | HTTP 403 | `Mock rejected registered key (HTTP 403)` |
| `owner` 用のモックAPIキー | ツール実行完了 | `mock-account:owner` |

成功時は登録画面で同じSecretに正しい値を上書きし、もう一度実行した。Codexの応答に加えて、アダプターのCloudWatchログで登録キー経由のツール実行完了を確認した。対照試験として、通常のモックキーを使う呼び出しでも同じアカウント応答を確認した。

再現コマンドは `python verify_codex.py --account owner --registered-key-failure` と `python verify_codex.py --account owner --registered-key-success`。事前に [READMEのセットアップ](../README.md) を行い、各ケースに対応する値を登録する。

検証範囲はモックMCPまで。実プリザンターでのAPIキー照合とツール実行は未検証。

[認証設計](authentication-and-key-registration.md) · [ログと削除結果](verification-evidence.md)
