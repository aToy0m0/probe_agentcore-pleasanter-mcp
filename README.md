# Cognito認証付き AgentCore MCP 検証サンプル

Codex CLIからCognitoでログインし、Amazon Bedrock AgentCore Gateway経由でモックのMCPサーバーを呼び出すサンプルです。ログインしたユーザーごとに、AWS側で別のAPIキーを選びます。MCPサーバーはLambda上で動作し、検証用ツールを1つ提供します。

Gatewayは公開HTTPSエンドポイントです。専用Cognitoクライアントのアクセストークンを要求し、自己登録を無効にしています。モックのFunction URLにはIAM認証を設定しています。インターセプターは `tools/list` でモックの一覧を中継し、`tools/call` でクライアントから渡された予約引数を署名付きの本人情報で置き換えます。

## 構成図

### 実行・認証

![Cognito認証からモックMCPまでの実行経路](docs/architecture/agentcore-mcp-runtime.png)

### デプロイ・検証

![CDKデプロイと検証Lambdaの実行経路](docs/architecture/agentcore-mcp-deployment.png)

## デプロイ

Node.js、npm、Python 3（`python` コマンド）、AWS認証プロファイルが必要です。このサンプルのデプロイ先は `us-east-1` です。対象アカウントとリージョンでCDKのブートストラップを済ませてください。下の `deployment-profile` は使用するAWSプロファイル名に置き換えます。

```sh
npm ci
npm run cdk -- synth --profile deployment-profile
npm run cdk -- diff --profile deployment-profile
npm run cdk -- deploy --profile deployment-profile
```

CloudFormationが `owner` と `codex` のテストユーザーを作り、Secrets ManagerがパスワードとダミーAPIキーを生成します。デプロイ時の検証Lambdaは、匿名・無効トークンの拒否、モックURLのIAM認証、モック由来のツール一覧、ユーザー別の呼び出し結果を確認します。結果はスタック出力 `Verification` に表示されます。

## Codex CLIから接続する

1. リポジトリルートに `.codex/` を作り、`config.toml.example` を `.codex/config.toml` としてコピーします。スタック出力の `GatewayUrl` と `ClientId` を設定に入れます。Cognitoクライアントは公開クライアントで、PKCEを使います。
2. リポジトリルートで `codex mcp login pleasanter_probe --no-browser` を実行します。表示された認可URLの `redirect_uri` をURLデコードし、コールバックURLを確認します。Gatewayごとに変わる末尾も含めて控え、Ctrl+Cでこの試行を終了します。
3. 確認したURLを `.codex/config.toml` のOAuthセクションに `callback_url` として追加し、同じ値を環境変数 `PROBE_CALLBACK_URL` に設定して、もう一度CDKでデプロイします。PowerShellなら `$env:PROBE_CALLBACK_URL = '<確認したコールバックURL>'` です。以後、このスタックを更新するときも同じ値を指定します。
4. `codex mcp login pleasanter_probe` を実行し、ブラウザで `owner` として認証します。パスワードは、スタック出力 `OwnerLoginSecret` が指すSecrets Managerの `password` の値です。
5. Windowsでは `python verify_codex.py --account owner` を実行します。Codex CLI実行ファイルが既定名と違う場合は `--codex-bin` で指定します。macOS・Linuxでは `python verify_codex.py --codex-bin codex --account owner` を使います。

実環境では、Codexがブラウザ認証後にモック固有のツール説明を取得し、`mock___whoami` の結果として `mock-account:owner` を受け取りました。AWS内の検証では、同じツールが `codex` ユーザーに `mock-account:codex` を返すことも確認しています。

## APIキー登録スタック

任意で、既存の検証用CognitoプールにAPIキーの登録画面を追加できます。Gatewayスタックを先にデプロイし、出力の `PoolId` と、そのプールのCognitoログインオリジンを指定します。ログインオリジンには `/oauth2` などのパスを付けません。

```sh
npm run cdk -- deploy -c registration=true --profile deployment-profile \
  --parameters UserPoolId=YOUR_USER_POOL_ID \
  --parameters LoginOrigin=https://YOUR_DOMAIN.auth.us-east-1.amazoncognito.com \
  --parameters IdentityProvider=COGNITO \
  --parameters Environment=probe
```

1. 登録スタック出力の `RegistrationUrl` を開き、Gateway試験と同じCognitoユーザーでログインします。
2. キーを登録します。Secrets Managerでこの操作が作った `pleasanter-mcp/probe/` 配下のSecretの完全なARNを控えます。検証は最初に1ユーザーで実行してください。
3. `PROBE_REGISTERED_SECRET_ARN` にそのARNを設定し、Gatewayスタックを再デプロイします。Codex接続で `PROBE_CALLBACK_URL` を設定していた場合は、その値も維持します。
4. 無効なテスト値で登録した場合は `python verify_codex.py --registered-key-failure` を実行します。その後、`owner` であればGatewayスタック出力 `OwnerSecret` の `apiKey` を登録画面で上書きし、`python verify_codex.py --registered-key-success` を実行します。ログインしたユーザーに対応するモック用キーを使ってください。

アダプターは、Cognitoで確認した本人に対応する登録済みSecretを読み、モックMCPに渡します。失敗時はモックのHTTP 403、成功時は `mock-account:owner` をCodexで確認できます。検証にはモック用キーとダミー値を使います。

両スタックを片付ける場合は、先に `npm run cdk -- destroy -c registration=true --profile deployment-profile` で登録スタックを削除します。登録操作で作成したSecretはスタック削除後も残るので、検証で作成したSecretをSecrets Managerで個別に削除してください。

## 後片付け

`npm run cdk -- destroy --profile deployment-profile` で検証スタックを削除します。テストユーザー、シークレット、Lambda、ロググループなどが削除対象です。KMS鍵は7日間の削除待ちに入ります。CDKの共有ブートストラップリソースと、そこに発行したテンプレート資産は検証スタックとは別管理です。
