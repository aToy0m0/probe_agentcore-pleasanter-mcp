# 認証とAPIキー登録の設計

APIキー登録スタックを追加すると、Gatewayに接続するCognitoユーザーがブラウザーから自分のモック用APIキーを登録できる。両スタックは同じCognitoユーザープールを使い、Gateway接続用とキー登録用に別々の公開OAuthクライアントを持つ。

| スタック | 役割 |
| --- | --- |
| Gateway検証 | Cognito、AgentCore Gateway、リクエストインターセプター、アダプター、モックMCPを用意する。 |
| APIキー登録 | Cognitoの既存プールに登録専用クライアントを追加する。API GatewayとLambdaで登録画面とAPIを提供し、登録済みのキーをSecrets Managerに保存する。 |

1. 登録画面はCognitoへ遷移し、認可コードをPKCEで交換する。登録APIはトークンの発行元、クライアント、種類、操作別スコープを確認する。
2. 登録Lambdaは、検証済みの `iss` と `sub` を改行で連結してSHA-256を計算する。発行元とユーザー固有IDの組でSecretを選ぶため、同じユーザーによる再登録では同じSecretの現行バージョンが更新される。
3. CodexはCognitoのアクセストークンでGatewayに接続する。インターセプターはCognitoの `GetUser` で本人を照合し、検証済みの本人情報と呼び出し情報にKMSで署名する。アダプターは署名を検証する。
4. 登録キーを試す呼び出しでは、アダプターが本人情報からSecret名を再計算し、設定されたSecretのARNと一致する場合だけ現行バージョンを読む。有効状態のキーを `X-API-Key` に設定し、SigV4で署名したリクエストをモックLambdaのFunction URLへ送る。

通常の呼び出しとツール一覧の取得では、検証スタックが生成したモック用キーを使う。登録キーを試すときは `mock___whoami` の引数 `testRegisteredKey` を有効にする。検証用アダプターの登録キー読み取り権限は、デプロイ時に指定するSecret一件に限定される。

[デプロイ手順](../README.md) · [検証結果](registered-key-validation.md) · [ログと削除結果](verification-evidence.md)
