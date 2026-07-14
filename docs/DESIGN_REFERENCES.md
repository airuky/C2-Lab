# 設計参照と安全な採用境界

## この文書の目的

C2 Lab Framework が、既存資料や実装からどの**抽象的な設計概念**を学び、何を意図的に採用しなかったかを記録します。参照元のコード、UI、文言、画像、ブランド資産はコピーしていません。このプロジェクトは参照元を依存関係、派生コード、実行時コンポーネントとして使用しません。

安全性の中心は、実運用能力を制限付きで再現することではなく、危険な能力へ至るコード経路を最初から持たないことです。

## 参照スナップショット

ローカルで読み取り専用に調査した比較対象は、Ramune-C2 の commit `f194494` です。調査時には主に次のファイル群を参照しました。

| 観点 | 参照元内のファイル | 読み取った抽象概念 |
| --- | --- | --- |
| process と役割 | `go.work`, `server/main.go`, `agent/main.go`, `server/api.go` | controller、browser、実行processの責務分離 |
| state と task | `server/session.go`, `server/handler.go` | 中央store、task、result、bounded queue |
| audit と event | `server/audit.go`, `server/events.go` | current stateと時系列記録、live notificationの分離 |
| API とUI | `server/api.go`, `server/web/index.html` | summary/detail、KPI、navigation、filter |
| 導入教材 | `ONBOARDING.md`, `README.md` | 短いarchitecture説明とkey-files導線 |

参照元の実装を正解やsecurity baselineとして扱ってはいません。参照元に含まれる実運用機能、永続data、外部通信、自由形式taskは、本教材の要件と安全境界に適合しません。

## 採用した設計概念

### 1. Process separation

本教材では、次の三つを別の実行主体として観察します。

- Teamserver: 認証、validation、中央state、task lifecycleの唯一の正本
- Browser Operator: read modelの表示、固定taskの冪等な登録、queued taskの取消、filterとnavigation
- Foreground Node: 同じPCからpollし、固定された合成処理またはNode-private workspace内の固定synthetic playbookだけを実行

分離の目的は、remote controlを実現することではなく、「誰がstateを決め、誰が表示し、誰がresultを返すか」を学ぶことです。通信範囲は常にloopbackであり、Nodeはservice化も外部接続も行いません。

### 2. Typed task ledger

taskは自由形式commandではなく、Teamserverが管理する型付きledger entryです。

| 項目 | 学習上の役割 |
| --- | --- |
| task ID | 一つのtask recordのidentity |
| correlation ID | request、event、resultを横断する関連ID |
| Node ID | taskの所有先 |
| type / payload | 固定registryとexact schemaで検証する入力 |
| status | `queued`, `dispatched`, `completed`, `failed`, `timeout`, `cancelled`, `expired` |
| lifecycle timestamps | queue待ち時間と処理時間の観察 |
| queue TTL | 未配送taskをboundedな待機時間で閉じる |
| delivery attempts | 応答消失時の期限内再配送の観察 |
| result | bounded JSON object。自由出力や実host dataではない |

内部recordには認証情報、monotonic deadline、任意のidempotency keyを保持できますが、公開summaryへ秘密やdeduplication metadataを含めません。Node、task、eventのAPI表現は、内部stateをそのままserializeせず、明示した公開fieldだけで構成します。

### 3. Structured audit and event

current stateと「何が起きたか」は別のviewです。event / audit recordは、少なくとも次のfieldを持つ構造化dataとして扱います。

- `sequence`: retained history内の順序を読むための番号
- event ID、時刻、kind、level、actor
- Node ID、task ID、correlation ID
- schemaが限定されたdata object

`sequence` はTeamserver process内の並びを安定して観察するための値です。Resetはoperational stateとretained eventを消去したうえで `lab.reset` を記録し、process内counterは進み続けます。auditはReset自体を含むbounded historyとして残ります。Teamserver restartではauditとcounterを含む全memory stateが失われます。sequenceは改ざん耐性や永続監査を保証しません。

`task.cancelled`、`task.expired`、`task.pruned`、`node.session_expired`も同じ固定schemaで記録します。取消、queue期限、session期限、retention整理を暗黙に消さず、actor、遷移元、遷移先、固定reasonで追跡します。

`GET /lab/audit` は、このbounded memory historyのread-only projectionです。`GET /lab/report` は、Node数、task lifecycle、retention設定を同じstateから集計するread-only projectionです。どちらもtaskを登録せず、Nodeへ指示せず、追加の永続dataを作りません。

### 4. KPI, navigation, and filters

KPI cardは数値を飾るためではなく、関連するread modelへ移動する入口として使います。filterは、Node、task status（`cancelled` / `expired`を含む）、lifecycle、event kind / levelなど、すでに取得した安全なrecordを絞り込む表示機能です。

- KPI、navigation、filterはserver-side authorizationを置き換えない
- filter変更はtaskやNode stateを変更しない
- hidden rowも認可済みAPI responseの一部であり、秘密を含めない
- lifecycle filterは状態遷移を学ぶためのprojectionであり、task再実行機能ではない
- Resetにはfilter解除とは別の確認を要求する

### 5. Fixed playbook and bounded evidence

`purple_lab`のplaybookは自由形式commandの別名ではありません。固定IDが、Node-private temporary workspace内の固定synthetic fixture、固定順序のstep、固定ATT&CK mapping、task固有schemaで検証できるbounded evidenceを選ぶtyped operationです。

実行自体は別プロセスNodeが実file I/Oとして行いますが、Operatorから渡せるのはplaybook IDだけです。path、content、command、args、URL、host、stepをtask payloadへ追加できません。TeamserverはNode resultを信頼せず、queued taskと一致するexact result schemaを検証してから確定します。

Ramune-C2のoperational capabilityは移植せず、task/result/auditを複数stepの観察へ拡張した独立の教材設計です。ATT&CK mappingはeducational metadataであり、実targetでのtechnique実行や検知coverageを保証しません。

### 6. Bounded lifecycle controls

queue TTL、queued taskの取消、task登録のidempotency key、terminal-only retention、stale sessionの60秒TTLは、typed ledgerを有限かつ観察可能に保つcontrol-plane機能です。いずれもNodeへ新しい処理を渡さず、task type、payload、result schema、loopback境界を拡張しません。

- queue TTLは既定300秒、指定時5〜86400秒で、`queued`だけを`expired`にする
- cancelは`queued`だけを`cancelled`にし、同じ取消の再送を冪等にする
- 任意`Idempotency-Key`は同じNode、type、payload、queue TTLの再送だけを同じretained taskへ収束させ、Nodeには公開しない
- stale offlineは60秒間回復可能とし、期限後はsessionと未処理taskを閉じる
- 500件到達時は最古のterminal taskだけを整理し、`queued` / `dispatched`を削除しない

## 安全なAPIとdata model

| API / read model | 用途 | 境界 |
| --- | --- | --- |
| `/lab/overview` | KPIと全体snapshot | bounded aggregate、秘密なし |
| `/lab/nodes` | 登録Nodeの公開summary | session tokenと実host情報を含めない |
| `GET /lab/tasks` | typed task ledger | fixed taskだけ、bounded payload/result |
| `POST /lab/tasks` | task登録 | strict payload、bounded queue TTL、任意idempotency key |
| `POST /lab/tasks/{task_id}/cancel` | queued taskの取消 | operator認証、queued-only、冪等cancel |
| `/lab/events` | lifecycle event | bounded memory history |
| `/lab/audit` | sequence順のstructured audit view | read-only、非永続、監査保証なし |
| `/lab/report` | stateから計算した集計 | read-only、元stateを変更しない |

UI filterや将来のdetail endpointを追加する場合も、queryは固定fieldと小さなlimitだけを許可します。query、sort、templateをcodeとして評価しません。

## 採用しない境界

次の領域は参照対象に存在していても、本教材へ取り込みません。

- loopback外のlistener、controller、transport、relay
- payload、stager、binary build、配布機能
- 自由形式command、script、operator console
- workspace外のfile、process、network、user、credential、画面など実host dataの取得
- background実行、自動起動、永続化
- traffic shaping、秘匿、難読化、回避
- pivot、proxy、peer-to-peer、lateral movement
- scheduler、webhook、外部notification
- credential store、永続operator account、multi-tenant RBAC
- runtime plugin、extension、dynamic module loading
- database、JSONL audit、sessionやtaskのdisk保存

認証やallowlistを追加しても、これらを安全な教材機能へ変換できるわけではありません。本教材では能力そのものを実装しません。

## 命名の方針

- 実行主体は `Teamserver`, `Operator`, `Node` と呼ぶ
- 処理単位は `task`、時系列記録は `event` / `audit` と呼ぶ
- `profile` は通信偽装ではなく固定taskのcapability subsetを意味するため、UIでは `Capability profile` と説明する
- 実運用を連想させる名称は比較説明の中だけで使う
- 参照元の名称、配色、ロゴ、mascot、UI copyは再利用しない

## 変更時の確認

設計参照を理由に機能を追加するときは、次を確認します。

1. 追加はcontrol-planeの観察またはread-only projectionか。
2. loopback以外の通信や実host dataを必要としないか。
3. taskは固定名、exact schema、bounded resultか。
4. 内部secretが公開DTO、audit、report、UIへ出ないか。
5. filterやreportがstateを変更しないか。
6. Resetではoperational stateだけが消え、auditにResetが残ること、restartではaudit、sequence、tokenを含む全memory stateが消えることをtestしたか。
7. 安全境界をtestと文書の両方で説明できるか。
8. `RUN_PLAYBOOK`は`purple_lab`だけに属し、playbook ID以外のoperator-controlled dataをruntimeへ渡さないか。
9. workspace rootをcallerから受けず、logical artifact registry、fixture、step、ATT&CK mappingが実装固定か。
10. evidenceはboundedで、absolute path、raw data、secretを含まないか。
11. cleanup、session失効、unknown ID、extra field、workspace containmentをtestしたか。
12. queue TTL、cancel、idempotency、terminal retentionがNode capabilityを増やさず、queued / dispatchedを誤って削除しないか。
13. stale sessionの回復猶予と自動失効がevent / audit / testで区別できるか。
