# CloudWatchの抜粋と削除結果

登録済みキーを試したときの、アダプターLambdaのCloudWatchメッセージを抜粋した。

| ケース | CloudWatchメッセージ |
| --- | --- |
| 無効な登録キー | `Registered key probe: mock HTTP 403` |
| 正しい登録キー | `Registered key probe: mock tool call completed` |

メッセージ本文だけを掲載した。ケースごとのCodex応答は[検証結果](registered-key-validation.md)を参照。

削除前に退避したイベントの集計は次のとおり。

| イベントの種類 | 登録スタック | Gateway検証スタック |
| --- | ---: | ---: |
| CloudWatchのLambdaイベント | 70 | 767 |
| CloudFormationの状態遷移イベント | 58 | 171 |
| CloudTrailの管理イベント | 28 | 139 |

CloudWatchには1回のLambda呼び出しで複数のイベントが記録される。表の数字は退避したイベント件数。

| 後片付けの対象 | 削除時に確認した結果 |
| --- | --- |
| 登録スタックとGateway検証スタック | 両方が `DELETE_COMPLETE`。対象のLambda、IAMロール、ロググループとGatewayを個別に照合した。 |
| 登録操作で作成されたSecret | スタック外で作成された検証用Secretを個別に削除し、存在しないことを確認した。 |
| CDKのテンプレート資産 | 対象2スタックに帰属するテンプレートのS3バージョン8件を個別に削除し、残存を確認した。 |
| KMS署名鍵 | スタック削除後に `PendingDeletion` を確認した。 |

[認証設計](authentication-and-key-registration.md) · [デプロイと後片付けの手順](../README.md)
