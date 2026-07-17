# 設計参照と安全な採用境界

## この文書の目的

C2 Lab Framework が、既存資料や実装からどの**抽象的な設計概念**を学び、何を意図的に採用しなかったかを記録します。参照元のコード、UI、文言、画像、ブランド資産はコピーしていません。このプロジェクトは参照元を依存関係、派生コード、実行時コンポーネントとして使用しません。

安全性の中心は、実運用能力を制限付きで再現することではなく、危険な能力へ至るコード経路を最初から持たないことです。

## 公式資料から参照した抽象概念

製品固有のcode、protocol、UI、brand、運用手順ではなく、中央state、複数閲覧者への共有、非同期task history、操作主体のattributionというcontrol-plane概念だけを参照しました。

### Cobalt Strike

- [Starting the Team Server](https://hstechdocs.helpsystems.com/manuals/cobaltstrike/current/userguide/content/topics/welcome_starting-cs-team-server.htm): client / Team Serverの責務分離
- [Distributed and Team Operations](https://hstechdocs.helpsystems.com/manuals/cobaltstrike/current/userguide/content/topics/welcome_distributed-and-team-ops.htm): 複数clientが共有stateとevent logを見る概念
- [Data Model](https://hstechdocs.helpsystems.com/manuals/cobaltstrike/current/userguide/content/topics_aggressor-scripts/as_data-model.htm): serverが中央modelを保持しclientへbroadcastする概念

### Sliver

- [Getting Started](https://sliver.sh/docs/?name=Getting+Started): 非同期taskの作成・送信・完了と、別Operatorがtask resultを参照する概念
- [Multi-player Mode](https://sliver.sh/docs/?name=Multi-player+Mode): 中央serverへ複数Operatorが接続する役割分離
- [Custom Clients](https://sliver.sh/docs/?name=Custom+Clients): event streamをclientへ通知するAPI概念
- [Beacons vs Sessions](https://sliver.sh/tutorials/?name=2+-+Beacons+vs+Sessions): check-in型の非同期task / result lifecycle

### Mythic

- [Operators](https://docs.mythic-c2.net/operators): admin、operator、spectatorと複数Operatorの共同作業
- [Operational Pieces](https://docs.mythic-c2.net/operational-pieces): event feedとOperator間の共有message
- [Overview](https://docs.mythic-c2.net/home): taskの発行者、comment、tagを含むcontextual data model

本教材が採用するのは、同じPC上の三つの固定role、taskの`created_by`、同一lockから作るcursor snapshot、そのsnapshotのread-only Graph View、最大3件のfixed-playbook operation、boundedなplain-text共有noteだけです。各製品のremote multiplayer listener、operator transport、WebSocket / gRPC stream、payload、implant、shell、実target data modelは採用しません。

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
- Browser Operator: 固定roleのread model表示、cursor short poll、permissionが許す固定taskの冪等な登録・queued task取消・共有note・Reset、filterとnavigation
- Foreground Node: 同じPCからpollし、固定された合成処理またはNode-private workspace内の固定synthetic playbookだけを実行

分離の目的は、remote controlを実現することではなく、「誰がstateを決め、誰が表示し、誰がresultを返すか」を学ぶことです。通信範囲は常にloopbackであり、Nodeはservice化も外部接続も行いません。

### 2. Typed task ledger

taskは自由形式commandではなく、Teamserverが管理する型付きledger entryです。

| 項目 | 学習上の役割 |
| --- | --- |
| task ID | 一つのtask recordのidentity |
| correlation ID | request、event、resultを横断する関連ID |
| Node ID | taskの所有先 |
| `created_by` | taskを登録した認証済みOperator principal。Browserから指定できず、Node制御には使わない |
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

`task.cancelled`、`task.expired`、`task.pruned`、`node.session_expired`も同じ固定schemaで記録します。取消、queue期限、session期限、retention整理を暗黙に消さず、actor、遷移元、遷移先、固定reasonで追跡します。Operatorによるtask登録、取消、共有note、Resetのactorには、認証済みsessionの`principal_id`を使います。noteのaudit entryは本文を複製しません。

`GET /lab/audit` は、このbounded memory historyのread-only projectionです。`GET /lab/report` は、Node数、task lifecycle、retention設定を同じstateから集計するread-only projectionです。どちらもtaskを登録せず、Nodeへ指示せず、追加の永続dataを作りません。

### 4. KPI, navigation, and filters

KPI cardは数値を飾るためではなく、関連するread modelへ移動する入口として使います。filterは、Node、task status（`cancelled` / `expired`を含む）、lifecycle、event kind / levelなど、すでに取得した安全なrecordを絞り込む表示機能です。

- KPI、navigation、filterはserver-side authorizationを置き換えない
- filter変更はtaskやNode stateを変更しない
- hidden rowも認可済みAPI responseの一部であり、秘密を含めない
- lifecycle filterは状態遷移を学ぶためのprojectionであり、task再実行機能ではない
- Resetにはfilter解除とは別の確認を要求する

### 5. Local relationship graph and bounded operation composition

`Graph View`は、新しい探索能力ではなく、既存の`GET /lab/sync` snapshotをBrowser内で関連付けるread-only projectionです。`C2トポロジー`は`nodes.id`と`tasks.node_id`、taskの`operation_id` / `operation_step`、`payload.playbook`を使い、Node → task → playbookのhandoffを表示します。`Synthetic Attack Path`は、さらに次の固定relationshipを重ねます。

- `exercise.node_id` / `exercise.task_ids`とNode / task
- completed taskのvalidated `result.attack_techniques`
- scenario catalogの`detections[].playbook` / `technique_id`
- exercise alertの`task_id` / `rule_id` / `technique_id`
- exerciseのfixed containment state

catalog preview、queued task、未完了relationshipは破線の`PLANNED`、completed task result、matched alert、適用済みcontainmentは実線の`OBSERVED`として表示します。plannedは実行済みを、observedは実環境の到達可能性や検知coverageを意味しません。どちらも固定LAB metadataとsynthetic fixtureの状態だけです。Graphのfocus変更やdetail handoffはserver stateを変更せず、実host、domain、user、group、credential、process、network relationshipを列挙するrequestも作りません。

`Operation Builder`は自由形式task consoleではなく、既存の固定playbookをまとめてqueueするbounded composerです。`POST /lab/operations`は`task_write`を要求し、exact bodyの`node_id`、1〜3件のordered `steps`、任意の`queue_ttl_seconds`だけを受けます。各stepは`{"playbook":"<固定4 IDのいずれか>"}`だけです。

Teamserverは一つのstate lock内で、全stepのschema、activeな`purple_lab` profile、task / playbook queue上限、retention slot、queue TTLを先に検証します。全taskをFIFO順で作成できる場合だけoperationを確定し、一件でも失敗すれば一件も作りません。任意`Idempotency-Key`は同じactor、Node、ordered steps、queue TTLの再送だけを同じoperationへ収束させます。

Operator向けtaskにはgrouping用の`operation_id`と1始まりの`operation_step`を付けますが、Node envelopeからは除外します。Nodeが受けるtype / payloadは従来の`RUN_PLAYBOOK`とfixed playbook IDだけで、operation全体、他step、Operator metadataは渡しません。`operation.queued` eventはoperation ID、step数、task IDだけを記録し、auditはoperation IDをcorrelationとして固定reasonを残します。どちらもpayload、evidence、path、contentを複製しません。

operationは永続campaign、scheduler、conditional workflow、自律agentではなく、単体ではexercise alertやcontainment対象を作りません。検知と封じ込めは、固定scenarioが所有するtaskのvalidated resultからだけ導出します。この分離により、C2トポロジーとtask detailでoperationの完了結果を確認しても、それをdetected exerciseや実環境のattack pathと誤認しません。

### 6. Fixed playbook and bounded evidence

`purple_lab`のplaybookは自由形式commandの別名ではありません。固定IDが、Node-private temporary workspace内の固定synthetic fixture、固定順序のstep、固定ATT&CK mapping、task固有schemaで検証できるbounded evidenceを選ぶtyped operationです。

実行自体は別プロセスNodeが実file I/Oとして行いますが、Operatorから渡せるのはplaybook IDだけです。path、content、command、args、URL、host、stepをtask payloadへ追加できません。TeamserverはNode resultを信頼せず、queued taskと一致するexact result schemaを検証してから確定します。

Ramune-C2のoperational capabilityは移植せず、task/result/auditを複数stepの観察へ拡張した独立の教材設計です。ATT&CK mappingはeducational metadataであり、実targetでのtechnique実行や検知coverageを保証しません。

### 7. Bounded lifecycle controls

queue TTL、queued taskの取消、task登録のidempotency key、terminal-only retention、stale sessionの60秒TTLは、typed ledgerを有限かつ観察可能に保つcontrol-plane機能です。いずれもNodeへ新しい処理を渡さず、task type、payload、result schema、loopback境界を拡張しません。

- queue TTLは既定300秒、指定時5〜86400秒で、`queued`だけを`expired`にする
- cancelは`queued`だけを`cancelled`にし、同じ取消の再送を冪等にする
- 任意`Idempotency-Key`は同じactor、Node、type、payload、queue TTLの再送だけを同じretained taskへ収束させ、Nodeには公開しない
- stale offlineは60秒間回復可能とし、期限後はsessionと未処理taskを閉じる
- 500件到達時はrunning exerciseが参照するtaskを保護しつつ、内部作成sequence上で最古のterminal taskだけを整理し、`queued` / `dispatched`を削除しない
- 受付済みresultは最大500件のbounded ACK tombstoneへ残し、task整理後にresponseだけを失ったNodeの同一再送を重複副作用なしで受理する

### 8. Local control-plane hardening

Operator session、固定RBAC、liveness / readiness、bounded metrics、secret-free access log、graceful shutdownは、localhost教材の制御面を誤操作しにくく、状態を検証しやすくする独立のhardeningです。外部listener、remote operator、実target task、永続account、transport、payloadといった実運用C2能力を追加するものではありません。

- Teamserver起動時だけ、8時間の`admin`、`operator`、`viewer` session URLを一つずつ発行し、refresh APIは設けない。expiry後は再起動で新URLを得る
- role mappingを`admin = read + task_write + exercise_write + containment_write + note_write + reset + operator_admin`、`operator = read + task_write + exercise_write + note_write`、`viewer = read`へ固定する
- Teamserverにはraw Operator tokenではなくSHA-256 digestだけをmemory内で保持し、`GET /lab/session`とadmin-onlyの`GET /lab/operators`にもtokenを返さず、`POST /lab/operators/{session_id}/revoke`で個別失効する
- 無効・期限切れ・失効済みsessionは401、有効でもpermission不足なら403に分ける
- `/healthz`はpublic liveness、`/readyz`はpublic runtime readiness、`/lab/metrics`は認証済みread-only projectionとして責務を分ける
- access logはmethod、normalized route、status、duration、固定principalだけをJSONで出し、token、query、動的ID、bodyを残さない。request workerはbounded queueへnon-blockingで渡し、sink停止時はdrop counterを増やす
- `SIGTERM`を`Ctrl-C`と同じcleanup経路へ通し、終了後のsession、state、metricsは復元しない
- session registryは64件に制限し、満杯時は最古のexpired / revokedだけを整理し、全activeなら429にする

これらのsessionは同じOS user上の一時的な役割分担であり、identity provider、永続user database、remote multi-user service、改ざん耐性のある監査を提供しません。

### 9. Atomic cursor sync

Browserごとに`/lab/overview`、`/lab/audit`、`/lab/session`を全件取得し続ける代わりに、`GET /lab/sync`はcurrent stateと新しいevent / auditだけを一つのresponseへまとめます。

- counts、Node、task、event delta、audit deltaを同じstate lock内で取得する
- processごとに新しい公開`stream_id`を返し、変更時はclientがcursor 0から取り直す
- `events_after` / `audit_after`を直前に処理したsequenceとして扱い、1回のpageを最大100件にする
- next cursor、high watermark、oldest available sequence、`has_more`を返す
- cursorがretentionより古い、またはhigh watermarkより新しい場合は`cursor_reset`を返し、client側cacheを置き換える
- UIは短い間隔の通常GETだけを使い、responseごとにconnectionを閉じる

snapshotのatomicityは「一回のresponseに異なるstate時点を混在させない」ためのものです。`stream_id`は再起動前後で同じsequence値が再利用される場合の混在を防ぎますが、永続identityではありません。cursorはmemory-only process sequenceであり、exactly-once delivery、永続offset、改ざん検知を保証しません。Resetではevent retentionが`lab.reset`から再開し、Teamserver restartではcounterも失われます。

SSE、WebSocket、gRPC stream、long pollを使わないのは、remote multiplayer APIを作らず、16件のbounded HTTP workerを長時間接続で占有しないためです。製品のevent broadcastから採用したのは、共有read modelとgap recoveryという抽象概念だけです。

### 10. Bounded operator collaboration

複数のlocalhost Browser tabで操作主体と状況を共有するため、taskは受付時の`principal_id`を`created_by`へ固定し、`operator.note`をcentral event feedへ追加できます。

- noteは`{"message":"..."}`だけを受ける1〜240文字のplain text
- `note_write`を持つadmin / operatorだけが投稿し、viewerを含む認証済みroleはread-only syncで閲覧する
- retained noteは最大100件で、上限時は`429 note_limit`
- 任意`Idempotency-Key`は同じactor・本文の再送だけを同じretained eventへ収束させる
- actorは認証済み`principal_id`から決め、request bodyから受けない
- note本文はNode task / poll / resultへ入れず、Nodeへ送らない
- auditはaction、actor、outcome、固定reasonだけを記録し、本文を複製しない
- report、metrics、access logも本文を保存しない

これはchat service、永続operation管理、remote collaboration、永続comment systemではありません。Operation Builderも最大3件のfixed-playbook queueに限られます。noteはeventと同じmemory-only stateで、ResetまたはTeamserver restartで失われます。Resetを跨ぐsyncはeventの`cursor_reset: true`でclientのReset前historyを置き換えます。Mythicのevent feed / commentやCobalt Strikeの共有event logから、boundedな共同観察だけを縮小採用しています。

## 安全なAPIとdata model

| API / read model | permission | 用途と境界 |
| --- | --- | --- |
| `/healthz` | public | 固定liveness。runtime readinessを判定しない |
| `/readyz` | public | runtime monitorの固定schema readiness。未準備は503 |
| `GET /lab/session` | `read` | 現在のtoken-free principal / role / permission |
| `GET /lab/sync` | `read` | 同一lock current snapshotとcursor-based event / audit delta。Graph Viewもこの既存dataだけを投影。pageは1〜100件 |
| `/lab/overview` | `read` | bounded KPIと全体snapshot、秘密なし |
| `/lab/nodes` | `read` | session tokenと実host情報を含めない公開summary |
| `GET /lab/tasks` | `read` | fixed taskだけのtyped ledger、bounded payload/result |
| `GET /lab/scenarios` | `read` | fixed ATT&CK mapping、detection metadata、containment allowlist |
| `GET /lab/exercises` | `read` | bounded alert / timeline / containment state |
| `POST /lab/tasks` | `task_write` | strict payload、bounded queue TTL、任意idempotency key |
| `POST /lab/operations` | `task_write` | active `purple_lab` Nodeへ1〜3件のordered fixed playbookを全件または0件でqueue。任意queue TTL / idempotency key |
| `POST /lab/tasks/{task_id}/cancel` | `task_write` | queued-only、冪等cancel |
| `POST /lab/exercises` | `exercise_write` | purple_lab Nodeと固定scenario IDだけ。任意idempotency key |
| `POST /lab/exercises/{exercise_id}/contain` | `containment_write` | admin-onlyのqueued取消またはserver-side tasking pause |
| `POST /lab/notes` | `note_write` | 1〜240文字のmemory-only plain-text note。Nodeへ送らない |
| `/lab/events` | `read` | bounded memory lifecycle history |
| `/lab/audit` | `read` | sequence順、非永続、監査保証なし |
| `/lab/report` | `read` | stateから計算し、元stateを変更しない |
| `/lab/metrics` | `read` | fixed-cardinality HTTP / lab / readiness集計。bodyとsecretなし |
| `GET /lab/operators` | `operator_admin` | token-free session一覧 |
| `POST /lab/operators/{session_id}/revoke` | `operator_admin` | 個別session失効。新session発行APIではない |
| `POST /lab/reset` | `reset` | Node operational stateを初期化。Operator sessionは維持 |

UI filterや将来のdetail endpointを追加する場合も、queryは固定fieldと小さなlimitだけを許可します。`/lab/sync`も`events_after`、`audit_after`、`limit`だけを受け、型、範囲、重複を検証します。query、sort、templateをcodeとして評価しません。

## 採用しない境界

次の領域は参照対象に存在していても、本教材へ取り込みません。

- loopback外のlistener、controller、transport、relay
- remote multiplayer listener、別hostのOperator client、WebSocket、SSE、gRPC event stream、long poll
- payload、stager、binary build、配布機能
- 自由形式command、script、operator console
- 任意step、条件分岐、scheduler、operator-defined workflowを持つoperation / autonomous agent
- workspace外のfile、process、network、user、credential、画面など実host dataの取得
- 実host、domain、user、group、credential、権限、network reachabilityを列挙するrelationship graph
- background実行、自動起動、永続化
- traffic shaping、秘匿、難読化、回避
- pivot、proxy、peer-to-peer、lateral movement
- scheduler、webhook、外部notification
- credential store、永続operator account、動的・multi-tenant RBAC
- runtime plugin、extension、dynamic module loading
- database、JSONL audit、sessionやtaskのdisk保存
- 永続chat / comment、外部message broker、共有noteのNode転送

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
14. Operator role / permissionが固定で、無効sessionの401と権限不足の403を区別しているか。
15. session DTO、access log、metricsにraw token、Authorization、query、動的ID、bodyが混入しないか。
16. `/healthz`と`/readyz`の責務、`/lab/metrics`の`read`認可をtestしたか。
17. task / cancel / Resetのactorが認証済み`principal_id`で、session revokeがadmin-onlyか。
18. `SIGTERM`時にserver、runtime、Node workspaceのgraceful cleanup経路が実行されるか。
19. `/lab/sync`のsnapshotが同じstate lockから作られ、cursor pageが最大100件か。
20. retentionより古いcursorと未来cursorが`cursor_reset`になり、clientがcacheを安全に置き換えるか。
21. sync UIがshort pollだけを使い、WebSocket、SSE、long pollを追加していないか。
22. taskの`created_by`が認証済みprincipal由来で、request bodyから偽装できないか。
23. Operator noteが1〜240文字、retained最大100件、`note_write` write、viewer read-onlyか。
24. note本文がNode、audit、report、metrics、access logへ複製されず、DOMでtextとして表示されるか。
25. Graph Viewが既存`/lab/sync` snapshotだけを使い、別のenumeration APIや実host relationshipを追加していないか。
26. planned / observedが固定catalog、validated task result、exercise alert / containmentからのみ決まり、実環境のattack pathやcoverageと表示していないか。
27. Operation Builderが1〜3件のfixed playbook、active `purple_lab`、bounded queue TTLだけを受け、全件または0件としてatomicにqueueするか。
28. operation metadataがNode envelopeへ渡らず、任意command、path、host、network destination、条件分岐、pluginへ拡張されていないか。
