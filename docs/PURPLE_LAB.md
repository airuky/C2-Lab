# Purple Lab実挙動ガイド

## 目的

`purple_lab`は、C2のtaskingを画面上の状態遷移だけで終わらせず、別プロセスNodeが実際にfileを作成・列挙・読取・hash・削除し、そのbounded evidenceをTeamserverへ返す演習profileです。

操作対象は、各Nodeが起動時に自分で作成する一時workspaceと、code内に固定されたsynthetic fixtureだけです。これは実target向けRed Team C2、remote administration、post-exploitation agentではありません。

## 起動

TeamserverとOperator UIは通常どおり起動します。実I/Oを観察するNodeだけを明示的に`purple_lab`で起動します。

```console
python3 -m c2lab node \
  --name purple-node-01 \
  --controller http://127.0.0.1:8765 \
  --profile purple_lab
```

`training`と`basic`は一時workspaceを作らず、`RUN_PLAYBOOK` capabilityも持ちません。

## 固定playbook

`RUN_PLAYBOOK`のpayloadは、次の形だけを許可します。

```json
{"playbook":"DISCOVERY_FIXTURES"}
```

| playbook | Nodeが実際に行う処理 | 教材上の観察点 |
| --- | --- | --- |
| `DISCOVERY_FIXTURES` | 固定synthetic fixtureをworkspace内で列挙 | discovery、logical artifact、sizeのevidence |
| `COLLECT_AND_STAGE` | 固定fixtureを読み、digestを計算し、固定manifestを作成 | collection/stagingとtask resultの対応 |
| `CREATE_CANARY` | 固定名・固定内容のcanary artifactを作成 | controlled changeと検知イベントの設計 |
| `CLEANUP` | 固定manifestとcanaryだけを削除 | task cleanupとsession cleanupの違い |

各playbookは固定順序の有限stepです。Operatorはcommand、step、path、filename、content、URL、host、argument、repeat count、timeoutを指定できません。

## 推奨演習

1. UIで`purple_lab` Nodeを選び、capabilityに`RUN_PLAYBOOK`があることを確認します。
2. `DISCOVERY_FIXTURES`を登録し、`queued → dispatched → completed`を追跡します。
3. `COLLECT_AND_STAGE`、`CREATE_CANARY`、`DISCOVERY_FIXTURES`を順に実行し、logical evidenceの変化を比較します。
4. `CLEANUP`後にもう一度`DISCOVERY_FIXTURES`を実行します。
5. task ID、correlation ID、event sequence、audit actionを対応付けます。
6. `training` Nodeへ同じtaskを登録し、UIとTeamserverの両方でprofile外として拒否されることを確認します。
7. TeamserverのReset後にNodeが再登録し、以前のworkspace artifactを引き継がないことを確認します。

## Evidenceの読み方

resultの`scope`は、`workspace: ephemeral-node-private`、`data: synthetic-fixtures-only`、`host_access: false`、`network_access: false`の固定objectです。`steps`と`evidence`は、固定処理が実際に完了したことを示すbounded metadataで、absolute path、raw artifact content、token、host metadataを含みません。

`attack_techniques`は、各stepが学習上どのATT&CK概念に対応するかを示す固定metadataです。実hostに対してtechniqueを実行した証明、検知coverageの保証、ATT&CK assessment結果ではありません。

TeamserverはNodeを信頼せず、task type、queued payload、enrolled Node identityに対応するexact result schemaを検証してからtaskを確定します。失敗結果も固定`error_code`だけを受け付けます。

## Workspace lifecycle

- `purple_lab` Nodeごとに異なるworkspaceを作成します。
- Nodeの正常終了時にworkspace全体を廃棄します。
- Teamserver Reset/restartでsessionが失効した場合、Nodeは旧workspaceを廃棄してから再登録します。
- `CLEANUP`は固定generated artifactだけを削除し、workspace自体はNode終了まで維持します。
- `SIGKILL`やOS crashではcleanupが実行されない場合があります。そのためworkspaceへ書く値は、最初から非機密のsynthetic fixtureだけです。

## 実装上の境界

- TeamserverとNodeはloopbackだけで通信する。
- workspace rootをC2Lab独自のCLI、API、task payloadから受け取らない。temporary baseはPythonの`TemporaryDirectory`とOSの通常設定に従う。
- caller-supplied pathを解決しない。
- caller-supplied filenameを持たず、固定logical artifact registryに対応するexclusive temporary fileだけを扱う。
- symlinkやregular fileでないartifactはfail closedにする。
- shell、subprocess、socket、archive、plugin、recursive deleteを使用しない。
- workspaceを配信するHTTP route、download、uploadを持たない。
- 同一Nodeのqueued playbookを3件に制限する。
- result全体を4096 bytes以下に保つ。

`TemporaryDirectory`自体はsecurity sandboxではなく、そのbase directoryはOSとPythonの通常のtemporary-directory選択（`TMPDIR`等）の影響を受けます。同じOS userがsource、process environment、workspaceを直接改変できる状況は、この教材の脅威モデル外です。

## 完了条件

- 実際のfile create/read/hash/deleteと、実target操作の違いを説明できる。
- `training`と`purple_lab`のcapability差を説明できる。
- 4 playbookのlifecycleをcorrelation IDで追跡できる。
- resultにabsolute path、host data、raw contentがないことを確認できる。
- `CLEANUP`、session失効、Node終了によるcleanupの違いを説明できる。
- payload schema、workspace ownership、result schema、network boundaryの四点から、remote C2へ転用できるinterfaceを持たない理由を説明できる。
