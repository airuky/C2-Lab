# C2 Lab Framework

C2 Lab Framework は、中央コントローラー、固定roleのOperator UI、定期的に poll する Node、非同期タスク、相関 ID、差分同期できるイベントログ、Operator間の共有メモに加え、Node専用の一時workspace内で完結する限定的な実I/Oを安全に学ぶための教材です。

Node は Teamserver とは**別の実プロセス**として動きますが、同じ PC の loopback にしか接続できません。別の物理端末を操作するエージェントではなく、実際のホストを制御する C2 でもありません。

> **LOCALHOST / EPHEMERAL WORKSPACE ONLY**
>
> `purple_lab` profileの`RUN_PLAYBOOK`は、Nodeが自分で生成した一時workspaceとsynthetic fixtureだけに実際のfile I/Oを行います。Operatorはplaybook ID以外のcommand、step、path、filename、content、URL、host、argumentを指定できません。shell、subprocess、host file、file transfer、OS列挙、loopback外通信は実装されていません。

## 学べること

- Teamserver を唯一の状態管理者にする client/server の責務分離
- memory-only Operator sessionと、`admin` / `operator` / `viewer`の固定RBAC
- enrollment と通常の Node session を分ける認証ライフサイクル
- poll による `queued → dispatched → completed / failed / timeout`、待機中の `cancelled / expired` と、応答消失に備えた期限内再送
- task ID と correlation ID を使った要求、結果、イベントの対応付け
- Node profile を許可リストの部分集合として扱う capability 制御
- 固定playbookによる実I/Oと、workspace所有・結果schema・session cleanupによる境界
- ATT&CK対応の固定scenario、検証済みresultからのdeterministic detection、Teamserver内だけのcontainment
- 現在状態と中央イベントログを分けて観察する方法
- 同一lockのsnapshot、sequence cursor、gap recoveryによる複数Operator UIの差分同期
- taskの`created_by`と、Nodeへ送られないboundedなOperator共有メモによる操作主体の区別
- liveness / readiness、認証済みmetrics、秘密を残さないaccess logによるlocalhost制御面の観察
- loopback、厳密なスキーマ、容量制限で教材の境界を保つ方法

## Architecture in 30 seconds

1. Browser Operator が固定taskを登録します。
2. Teamserver が入力を検証し、typed task ledgerとsequence付きeventを更新します。
3. 同じPCのforeground Nodeがpollし、固定処理またはNode-private workspace内の固定playbookを実行します。
4. UIはcurrent state、event、auditを同一snapshotから短い間隔で差分同期し、KPI、navigation、filterでlifecycleを観察します。reportは認証済みread-only APIとして取得できます。

```text
Browser Operator UI
        │ localhost HTTP + fixed-role Operator session
        ▼
Authoritative Teamserver
        ├── memory-only RBAC / token digests
        ├── strict schema / fixed task registry
        ├── in-memory Node state / typed task ledger
        ├── FIFO task dispatch
        ├── correlation IDs
        ├── bounded sequence audit
        ├── cursor sync / shared operator notes
        ├── read-only report / metrics projection
        └── liveness / readiness / secret-free access log
                    ▲               │
        result      │               │ next task
                    │               ▼
             Foreground Node process
             loopback HTTP polling only
```

Teamserver、Node、ブラウザは別の実行主体ですが、通信先は常に `127.0.0.1` または `localhost` です。Node の `online` は「同じ PC 上の Node プロセスが最近 poll した」という意味で、リモート端末への接続を意味しません。

Operator UI は Node を作成・接続・切断しません。別ターミナルから登録された Node をread-onlyに表示し、sessionの権限が許す場合だけ固定taskの冪等な登録、queued taskの取消、共有メモの投稿、Resetを提供します。task filter、lifecycle詳細、event / audit切替はread-onlyです。read-only reportはAPIから取得できます。KPI cardは関連viewへのnavigationとして機能し、filterは表示だけを変えてserver stateや認可を変更しません。profile外taskと切断済みNodeはselectorで無効化され、Teamserverでも再検証されます。

## 参考にしたアーキテクチャ概念

本教材は、実運用 C2 の機能を再実装したものではありません。公式資料から、制御面の役割分離と非同期状態遷移だけを参考にしています。

- Cobalt Strike の公式ガイドは、Team Server を中央コントローラー、共有データ、ログの管理主体として説明し、複数の operator client が共有状態とイベントログを参照する構造を示しています。本教材ではこれを「中央 Teamserver、同一lock snapshot、localhost UIのcursor同期」に縮小しました。参照: [Starting the Team Server](https://hstechdocs.helpsystems.com/manuals/cobaltstrike/current/userguide/content/topics/welcome_starting-cs-team-server.htm)、[Distributed and Team Operations](https://hstechdocs.helpsystems.com/manuals/cobaltstrike/current/userguide/content/topics/welcome_distributed-and-team-ops.htm)、[Data Model](https://hstechdocs.helpsystems.com/manuals/cobaltstrike/current/userguide/content/topics_aggressor-scripts/as_data-model.htm)
- Sliver の公式資料は、Beacon modeの非同期task lifecycleと、複数Operatorが中央serverのtaskや結果を共有するmodelを説明しています。本教材ではpoll/task/resultと共有read modelだけを、同じPC上の固定された無害な処理へ転用しました。参照: [Sliver Getting Started](https://sliver.sh/docs/?name=Getting+Started)、[Multi-player Mode](https://sliver.sh/docs/?name=Multi-player+Mode)、[Beacons vs Sessions](https://sliver.sh/tutorials/?name=2+-+Beacons+vs+Sessions)
- Mythic の公式資料は、複数Operator、spectator、event feed、task commentといった共同作業上の概念を説明しています。本教材では永続accountやoperationを採用せず、固定role、`created_by`、memory-only共有メモだけを採用しました。参照: [Operators](https://docs.mythic-c2.net/operators)、[Operational Pieces](https://docs.mythic-c2.net/operational-pieces)、[Overview](https://docs.mythic-c2.net/home)

ローカルのRamune-C2 commit `f194494` も読み取り専用で比較し、process separation、typed task ledger、structured audit / event、KPI / navigation / filterというcontrol-plane上の概念だけを独立に採用しました。参照元のcode、brand、UI asset、文言、実運用機能はコピーしていません。採用判断と不採用境界は[設計参照文書](docs/DESIGN_REFERENCES.md)に記録しています。

実 listener、payload 生成、外部 transport、remote multiplayer、WebSocket / SSE、任意コマンド、対話 shell、実ホスト収集、通信偽装、永続化は参考対象にも実装対象にも含めていません。Cobalt Strike、Sliver、Mythic および各製品名は、それぞれの権利者に帰属します。本プロジェクトは各製品と提携していません。

## 必要環境

- Python 3.11 以降
- 同じ PC 上の二つのターミナル
- localhost へ接続できるブラウザ

追加パッケージ、管理者権限、データベース、コンテナは不要です。

## クイックスタート

### 1. Teamserver を起動する

一つ目のターミナルで、リポジトリのルートから実行します。

```console
python3 -m c2lab teamserver --port 8765
```

Teamserver は `127.0.0.1:8765` だけで待ち受け、三つのOperator session URLと、一つのNode enrollment tokenを表示します。

| CLI出力 | principal | role / 用途 |
| --- | --- | --- |
| `Admin URL` | `local-admin` | read、task登録・取消、共有note、Reset、Operator session一覧・個別失効 |
| `Operator URL` | `task-operator` | read、task登録・取消、共有note |
| `Viewer URL` | `read-viewer` | read-only |
| `Node enrollment token` | — | Nodeの最初の登録だけに使う別の秘密 |

三つのOperator sessionは起動時に発行され、いずれも8時間で期限切れになります。URLはそれぞれのBearer tokenを`#token=...` fragmentに含みます。raw tokenはこの起動時出力とBrowser tabにだけ渡され、Teamserverのsession registryにはSHA-256 digestだけがmemory内で保持されます。

引数なしの `python3 -m c2lab` も、既定ポートで Teamserver を起動します。

### 2. Operator UI を開く

一連の演習を行う場合は、Teamserver が表示した `Admin URL` 全体を同じ PC のブラウザで開きます。権限差を確認するときは `Operator URL` または `Viewer URL` を別のBrowser tabで開きます。UI は fragment から token を読み取った後、アドレスバーから fragment を除去し、現在のタブの `sessionStorage` に保持します。接続後はprincipal、role、permissionsがUIに表示されます。

fragment は通常の HTTP リクエストには送信されません。URL、token、認証ヘッダーをスクリーンショット、ログ、課題提出物へ含めないでください。

### 3. Node を別ターミナルで起動する

二つ目のターミナルを、**同じ PC 上で**開きます。安全のため、enrollment token のオプションを省略し、非表示 prompt へ入力する方法を推奨します。

```console
python3 -m c2lab node \
  --name lab-node-01 \
  --controller http://127.0.0.1:8765 \
  --profile training
Node enrollment token:
```

明示的に渡す場合の完全な形式は次のとおりです。`TOKEN` は Teamserver が表示した値に置き換えます。実際の token は shell history に残るため、共有環境ではこの形式を避けてください。

```console
python3 -m c2lab node --name lab-node-01 --controller http://127.0.0.1:8765 --profile training --enroll-token TOKEN
```

登録に成功すると Node ID が表示され、Node は foreground で poll を開始します。バックグラウンドサービス、自動起動、永続化は行いません。

`127.0.0.1` は、そのコマンドを実行した PC 自身を表します。この Node コマンドを別の物理 PC で実行しても、元の PC の Teamserver には接続できません。

### 4. 合成タスクを観察する

Operator UI で Node を選び、最初は `PING` を登録します。Tasks と Events で、task が `queued`、`dispatched`、`completed` と進み、同じ correlation ID で対応付くことを確認します。

実I/Oを観察するときだけ、別のNodeを明示的に`purple_lab`で起動します。

```console
python3 -m c2lab node --name purple-node-01 --profile purple_lab
```

UIで`RUN_PLAYBOOK`を選び、`DISCOVERY_FIXTURES`、`COLLECT_AND_STAGE`、`CREATE_CANARY`、`CLEANUP`を順に登録します。操作対象はそのNodeが起動時に作成した一時workspaceだけです。

### 5. 停止する

Node のターミナルで `Ctrl-C` を押すと、可能な場合は切断を通知し、Node session を失効させて終了します。その Node の未処理 task は `failed` になります。その後 Teamserver のターミナルで `Ctrl-C` を押します。NodeまたはTeamserverが`SIGTERM`を受けた場合も同じgraceful shutdown経路を通り、可能なcleanupとsocket closeを行います。Teamserver 側のOperator / Node session、task、event、token はすべてmemory-onlyで、process終了時に失われます。Browser や停止していない Node process に古い token 文字列が残っていても、新しい Teamserver では使用できません。

## CLI

### Teamserver

```console
python3 -m c2lab teamserver [--port PORT]
```

| オプション | 既定値 | 説明 |
| --- | ---: | --- |
| `--port` | `8765` | `127.0.0.1` で使うポート。`0..65535` |

`--port 0` を指定すると、OS が空きポートを選び、実際の URL が起動時に表示されます。bind 先を指定するオプションはありません。

### Node

```console
python3 -m c2lab node --name NAME [--controller URL] [--profile PROFILE] [--poll-ms MS] [--jitter PERCENT] [--enroll-token TOKEN]
```

| オプション | 既定値 | 説明 |
| --- | --- | --- |
| `--name` | 必須 | Operator UI に表示する名前。1〜48文字 |
| `--controller` | `http://127.0.0.1:8765` | loopback Teamserver URL |
| `--profile` | `training` | `basic`、`training`、`purple_lab` |
| `--poll-ms` | `1000` | poll 間隔。`250..3000` ミリ秒 |
| `--jitter` | `0` | poll 間隔のランダム幅。`0..50` パーセント |
| `--enroll-token` | prompt | 省略時は shell history に残らない非表示入力 |

controller URL は `http` と `127.0.0.1` または `localhost` だけを許可します。`localhost` も内部では `127.0.0.1` へ正規化されます。認証情報、query、fragment、追加 path を含む URL と、loopback 以外の host は起動前に拒否されます。Node client は system proxy を無効化し、HTTP redirect を追跡しません。

## 認証ライフサイクル

```text
Teamserver start
   ├─ admin session ─────> Browser UI ──> Authorization: Bearer ...
   ├─ operator session ──> Browser UI ──> Authorization: Bearer ...
   ├─ viewer session ────> Browser UI ──> Authorization: Bearer ...
   └─ enrollment token ──> Node enroll
                              └─ per-node session token
                                   ├─ poll
                                   ├─ result
                                   └─ disconnect
```

| role | 固定permissions |
| --- | --- |
| `admin` | `read`, `task_write`, `exercise_write`, `containment_write`, `note_write`, `reset`, `operator_admin` |
| `operator` | `read`, `task_write`, `exercise_write`, `note_write` |
| `viewer` | `read` |

- 三つのOperator tokenとenrollment tokenは、Teamserver起動ごとに別々に生成されます。Operator sessionはすべて8時間で期限切れになり、raw tokenではなくSHA-256 digestだけがTeamserver memoryに残ります。refreshや追加発行APIはないため、期限切れ後に演習を続ける場合はTeamserverを再起動し、新しい三つのURLを使います。
- `GET /lab/session`は認証中sessionのprincipal、role、permissions、有効期限、状態をtokenなしで返します。
- `admin`だけが`GET /lab/operators`でtokenを含まないsession一覧を読み、`POST /lab/operators/{session_id}/revoke`で個別に失効できます。唯一のactive adminは自分自身を失効できません。
- tokenがない、無効、期限切れ、失効済みの場合は`401 Unauthorized`です。有効なsessionでも必要permissionを持たない場合は`403 Forbidden`です。UIの無効化だけを認可境界にせず、Teamserverが各requestを再検証します。
- enrollment token は登録専用です。一回限りではなく、その Teamserver の実行中に複数 Node を登録できます。
- 登録後、Node ごとに異なる session token が発行されます。Node は以後 enrollment token ではなく、その session token と Node ID を使います。
- Node session token は Node プロセスのメモリ内だけにあり、通常は画面へ表示されません。
- 正常な disconnect は、その Node session token を直ちに失効させます。未処理の `queued` / `dispatched` task は `failed` になり、切断済み Node への新規 task は拒否されます。
- pollが途絶えてstale offlineになったsessionは60秒の回復猶予を持ちます。猶予内のpollは同じNode IDをonlineへ戻します。60秒を過ぎるとsession tokenを失効させ、未処理taskを`failed`にします。
- Reset は現在の Node とNode session tokenを破棄しますが、Operator sessionとenrollment tokenは変えません。実行中 Node は、保持している enrollment token で再登録できます。
- Teamserver を再起動すると、三つのOperator sessionとenrollment tokenを含む全memory stateが失われ、新しいtokenが発行されます。古い enrollment token を持つ Node は、新しい token で起動し直すまで登録できません。

これらは localhost 内の役割分離を学ぶための簡易認証です。同じ OS ユーザーを侵害した攻撃者に対する強固な分離ではありません。

## Node profile

profile は動的プラグインではなく、コード内に固定された許可リストの名前です。Node が登録時に申告する capabilities は、Teamserver が知る profile と完全一致しなければ拒否されます。

| profile | 許可タスク |
| --- | --- |
| `basic` | `PING`, `RUNTIME_STATUS`, `ECHO_TEXT`, `HASH_TEXT` |
| `training` | 上記に `WAIT`, `GENERATE_EVENT`, `SLEEP`, `EXIT` を追加 |
| `purple_lab` | `training` の全タスクと `RUN_PLAYBOOK` |

profile は固定レジストリを拡張できず、許可済みタスクの部分集合を選ぶだけです。

## 固定タスク

| タスク | `payload` | 結果と用途 |
| --- | --- | --- |
| `PING` | `{}` | 固定値 `PONG`。poll/task/result の確認 |
| `RUNTIME_STATUS` | `{}` | version、profile、uptime、完了数、poll 間隔。OS 情報は取得しない |
| `ECHO_TEXT` | `{"text":"hello-lab"}` | 入力した制限内文字列を返す |
| `HASH_TEXT` | `{"text":"hello-lab"}` | 入力文字列だけの SHA-256。ファイルは読まない |
| `WAIT` | `{"milliseconds":750}` | `0..2000` ミリ秒だけ foreground 処理を待つ |
| `GENERATE_EVENT` | `{"category":"training","severity":"info","message":"synthetic event"}` | 中央ログへ合成イベントを追加 |
| `SLEEP` | `{"interval_ms":2000,"jitter_percent":20}` | bounded poll設定を`250..3000 ms`、`0..50%`内で変更 |
| `EXIT` | `{}` | result acknowledgement後にforeground Nodeを正常停止 |
| `RUN_PLAYBOOK` | `{"playbook":"DISCOVERY_FIXTURES"}` | `purple_lab`専用。Node-private一時workspaceで固定playbookを実行し、bounded evidenceを返す |

`text` と `message` は 1〜240 文字です。`GENERATE_EVENT.category` は `training`、`telemetry`、`policy`、`severity` は `info`、`warning` の固定列挙です。`RUN_PLAYBOOK`は固定playbook IDだけを受け、同一Nodeの待機playbookは3件までです。余分な field、不正な型、範囲外の値、未定義 task は登録前に拒否されます。実行方法と安全境界は[Purple Lab実挙動ガイド](docs/PURPLE_LAB.md)を参照してください。

## ATT&CK検知・封じ込め演習

`purple_lab` Nodeを対象に、Operator UIの「ATT&CK演習」から次の固定scenarioを開始できます。Operatorが指定できるのは`node_id`と`scenario_id`だけで、rule、path、step、内容は指定できません。

| scenario | 固定playbook | educational mapping / detection strategy |
| --- | --- | --- |
| `DISCOVERY_COLLECTION` | `DISCOVERY_FIXTURES` → `COLLECT_AND_STAGE` | T1083 / DET0370、T1005 / DET0380、T1074.001 / DET0261 |
| `CANARY_REMOVAL` | `CREATE_CANARY` → `CLEANUP` | T1070.004 / DET0140。固定artifactだけを対象にする削除simulation |

Techniqueとdetection metadataはMITRE ATT&CKの公式ページ（[T1083](https://attack.mitre.org/techniques/T1083/)、[T1005](https://attack.mitre.org/techniques/T1005/)、[T1074.001](https://attack.mitre.org/techniques/T1074/001/)、[T1070.004](https://attack.mitre.org/techniques/T1070/004/)、[DET0370](https://attack.mitre.org/detectionstrategies/DET0370/)、[DET0380](https://attack.mitre.org/detectionstrategies/DET0380/)、[DET0261](https://attack.mitre.org/detectionstrategies/DET0261/)、[DET0140](https://attack.mitre.org/detectionstrategies/DET0140/)）へ対応付けています。thresholdとsignal名は小さなsynthetic fixture向けの教材固有値です。

Teamserverは、task固有schemaを通過して`completed`になったplaybookだけから固定alertを生成し、exercise timelineへ記録します。これは実環境のcoverageを主張する検知器ではありません。`exercise_write`を持つadmin / operatorが開始でき、`containment_write`を持つadminだけが次の固定actionを適用できます。

- `CANCEL_REMAINING`: そのscenarioに残る`queued` taskだけを取り消す
- `PAUSE_NODE_TASKING`: 上記に加え、Node sessionを維持したままTeamserverで新規queue / dispatchを停止する。解除はReset

containmentはOS process、firewall、account、network interfaceを操作しません。状態、alert、timeline、idempotency recordは最大50件のmemory-only exerciseとして保持され、ResetまたはTeamserver終了で消えます。

## 非同期 tasking

```text
Operator registers task
          │
          ▼
       queued ── Node poll ──> dispatched ── valid result ──> completed
          ├─ Operator cancel ─> cancelled
          ├─ queue TTL ───────> expired
          │                           │       safe handler error ─> failed
          └── session close ──────────┴──────────────────────────> failed
                                      └─ no result within 8 s ───> timeout
```

- `queued`: Teamserver が検証して受付済み。既定300秒のqueue TTL内でNodeの次回pollを待つ状態
- `dispatched`: Node に配送済み。配送時から 8 秒の期限を持ち、同じ session の次回 poll には同じ task を再配送する状態
- `completed`: Node が固定ハンドラーの結果を返した状態
- `failed`: Node が安全に処理失敗を返したか、disconnect / stale session失効により未処理taskが閉じられた状態
- `timeout`: 配送後 8 秒以内に結果が届かなかった状態
- `cancelled`: Operatorが配送前のqueued taskを取り消した状態
- `expired`: queuedのまま指定queue TTLを過ぎ、Teamserverが閉じた状態

一つの Node が同時に持つ `dispatched` task は一件だけです。待機 task は作成順に配送されます。queue TTLは既定300秒で、Operator APIでは5〜86400秒を指定できます。TTLと取消は`queued`だけに適用され、一度`dispatched`になったtaskは取消・queue期限切れの対象になりません。初回配送で `delivery_attempts` は 1 になり、結果未受領のまま同じ session が期限内に poll すると、Teamserver は active task を再配送して回数を増やします。再配送は 8 秒の deadline を延長しません。

各taskにはtask IDとは別にcorrelation IDが生成され、`created_by`には登録を認可したOperator sessionの`principal_id`が固定されます。`task.queued`、`task.dispatched`、`task.redelivered`、`task.completed`、`task.failed`、`task.timeout`、`task.cancelled`、`task.expired`、`task.pruned` のeventが同じcorrelation IDを持つため、一つの要求を時系列で追跡できます。各eventの `sequence` はTeamserver process内の順序を安定して比較するための値です。Reset後もcounterは進みますがretained eventは `lab.reset` から始まり、Teamserver restartではcounterを含む全memory stateが失われます。

Operatorのtask登録は任意の`Idempotency-Key`を受けます。8〜128文字の英数字と`-_.:`だけを許可し、retained memory state内で同じactor、Node、type、payload、queue TTLの再送を同じtaskへ収束させます。同じkeyを別actorまたは異なるrequestへ使うと`409 idempotency_conflict`です。UIは通信結果が不明な再試行で同じkeyを再利用し、正常受付または確定した4xx後に破棄します。key自体はtask、event、audit、reportへ公開しません。

Node は実行済み result を acknowledgement が返るまで memory 内の pending-result outbox に保持し、新しい task を poll する前に同じ result を再送します。Teamserver に届く前に request が失われた場合は再送で登録され、登録後の HTTP response だけが失われた場合も、同じ status と result の再送は冪等な成功になります。確定taskが通常一覧からretention整理された後も、最大500件のbounded ACK tombstoneが同一結果の再送へ同じresponseを返し、counterやeventを重複させません。異なる内容の再送は `409 result_conflict` です。Node process を終了すると outbox も失われ、tombstoneも永続化されないため、永続配送を保証する仕組みではありません。

`SLEEP`のpoll設定とNode側`tasks_completed`は、resultのacknowledgement後にだけcommitします。task timeout後などの確定4xxでresultが拒否された場合は、Nodeだけが設定を先行変更せず、Teamserverと同じ旧poll状態を維持します。

初回resultがtask固有contractに一致しない場合、taskは`dispatched`のまま確定せず、`task.result_rejected`をevent/auditへ記録します。記録するのはtask typeと固定reasonだけで、不正resultの内容はevent/auditへコピーしません。

Node は poll のたびに `last_seen` を更新します。正常な切断通知がない場合は、最後の poll から `max(8秒, poll間隔×3)` 後に `offline` と判定されます。このstale offlineは60秒間だけsessionが有効な回復可能状態です。期限内にpollすればonlineへ戻り、期限を過ぎると`node.session_expired`を記録してsessionを失効させ、未処理taskを`failed`にします。実行中Nodeは`401`を受けると保持中のenrollment tokenで新しいNodeとして再登録します。

| `status` | `session_active` | 意味 | 新規 task |
| --- | --- | --- | --- |
| `online` | `true` | 最近 poll した | 受付可能 |
| `offline` | `true` | stale後60秒以内。poll再開可能 | queuedとして受付可能 |
| `offline` | `false` | disconnectまたはstale session TTL失効済み | `409 node_disconnected` |

## 差分同期とOperator共有メモ

Browser UIは3秒ごとの短いpollで`GET /lab/sync`を取得します。Teamserverは一つのstate lockを保持したまま、counts、Node、task、event、auditと、それぞれのcursor metadataを同じsnapshotとして作ります。これはWebSocket、SSE、長時間接続ではなく、各responseで接続を閉じる通常のlocalhost HTTPです。

`events_after`と`audit_after`は直前に処理したsequence、`limit`は1回に返す履歴件数で、上限は100です。responseにはprocessごとに変わる公開`stream_id`、次のcursor、high watermark、retained historyの最古sequence、`has_more`、`cursor_reset`が含まれます。cursorがretentionより古い、または現在のhigh watermarkより新しい場合、UIは`cursor_reset`を見て保持中の該当historyを置き換えます。`stream_id`が変わった場合もUIは新旧processのsequenceを混在させず、cursor 0からretained historyを取り直します。通常時は受信済み履歴を再取得せず、新しいevent / auditだけを追加します。cursorは配送最適化のためのmemory-only sequenceであり、欠落のない永続監査や改ざん検知を保証しません。

`note_write`を持つ`admin`と`operator`は`POST /lab/notes`へ1〜240文字のplain textを投稿でき、`viewer`を含む認証済みroleは`/lab/sync`のevent feedで読めます。noteは`operator.note` eventとして最大100件を保持し、actorには認証済み`principal_id`を使います。note本文はNode task、Node poll、task payload / resultへ入らず、Nodeへ送信されません。auditはnoteのaction、actor、outcomeだけを記録し、本文を複製しません。access logとmetricsもrequest bodyを保持しません。

note投稿は任意の`Idempotency-Key`に対応し、同じactor・本文・keyの再送を同じretained eventへ収束させます。同じkeyを異なるnoteへ使うと`409 idempotency_conflict`です。retained noteが100件に達すると`429 note_limit`で拒否し、暗黙に既存noteを削除しません。noteはResetでeventとともに消れ、次のsyncではeventの`cursor_reset: true`によりUIがReset前のevent / note履歴をretained pageへ置き換えます。Teamserver restartでもnoteは失われます。共有メモへtoken、実在する人・組織・端末・credentialなどの秘密を書かないでください。

## API

すべての API は loopback からの要求と localhost の `Host` header だけを受け付けます。`POST` body は `application/json` です。HTTP response ごとに `Connection: close` を返し、idle keep-alive を保持しません。

### Operator API

静的UI、`/healthz`、`/readyz`は認証不要です。それ以外の`/lab/*`は`Authorization: Bearer <operator-token>`を要求します。ブラウザが `Origin` を送る書込要求では、同じ localhost origin だけを許可します。

| メソッド | パス | permission | body / 用途 |
| --- | --- | --- | --- |
| `GET` | `/healthz` | public | processがHTTPへ応答できることだけを示す固定liveness。runtime状態は判定しない |
| `GET` | `/readyz` | public | runtime monitorが処理可能なら`200 ready`、未準備・停止・異常なら`503 not_ready` |
| `GET` | `/lab/session` | `read` | 現在のtoken-free Operator session |
| `GET` | `/lab/sync?events_after=...&audit_after=...&limit=...` | `read` | 同一lockのcurrent snapshotとcursor-based event / audit delta。`limit`は1〜100 |
| `GET` | `/lab/overview` | `read` | 集計、Node、task、event のスナップショット |
| `GET` | `/lab/nodes` | `read` | Node 一覧 |
| `GET` | `/lab/tasks` | `read` | task 一覧 |
| `GET` | `/lab/scenarios` | `read` | 固定scenario、technique、detection metadata |
| `GET` | `/lab/exercises` | `read` | bounded exercise、alert、timeline、containment state |
| `GET` | `/lab/events` | `read` | 中央イベント一覧 |
| `GET` | `/lab/audit` | `read` | sequence順のbounded structured audit view |
| `GET` | `/lab/report` | `read` | Nodeとtask lifecycleのread-only集計 |
| `GET` | `/lab/metrics` | `read` | bounded HTTP集計、lab件数、readinessのread-only snapshot |
| `GET` | `/lab/operators` | `operator_admin` | tokenを含まないOperator session一覧 |
| `POST` | `/lab/tasks` | `task_write` | node ID、type、payload、任意queue TTL。固定taskを登録し、任意`Idempotency-Key`に対応 |
| `POST` | `/lab/tasks/{task_id}/cancel` | `task_write` | `{}`。queued taskだけを取消し、cancelledへの再送は冪等 |
| `POST` | `/lab/exercises` | `exercise_write` | `{"node_id":"...","scenario_id":"..."}`。固定scenarioを開始し、任意`Idempotency-Key`に対応 |
| `POST` | `/lab/exercises/{exercise_id}/contain` | `containment_write` | 固定`action`。adminだけがcontrol-plane containmentを適用 |
| `POST` | `/lab/notes` | `note_write` | `{"message":"..."}`。1〜240文字のmemory-only共有メモ、任意`Idempotency-Key`対応 |
| `POST` | `/lab/reset` | `reset` | `{}`。Node session、task、eventを初期化し、auditへResetを記録 |
| `POST` | `/lab/operators/{session_id}/revoke` | `operator_admin` | `{}`。指定Operator sessionを個別に失効 |

`POST /lab/tasks`の任意`queue_ttl_seconds`は5〜86400の整数で、省略時は300です。taskには認証済みprincipalを示す`created_by`が入り、Nodeへ渡すtask identityやpayloadには使いません。`Idempotency-Key`はHTTP headerで渡し、同じkeyを異なるtask requestへ再利用すると`409`です。取消済みtaskの再取消は同じ`cancelled` recordを返しますが、dispatchedまたは他のterminal taskは`409 task_not_cancellable`です。

task登録、取消、共有note、Resetのevent / audit actorには、総称の`operator`ではなく認証済みsessionの`principal_id`（既定では`local-admin`または`task-operator`）が入ります。これによりroleごとの操作をmemory-only history内で区別できますが、永続監査や本人性を保証するものではありません。共有noteのaudit entryには本文を入れません。

Teamserverは各HTTP responseについて、stderrへ1行JSONのaccess logを出します。fieldは時刻、固定event名、`GET` / `POST` / `OTHER`へ正規化したmethod、固定ラベルへ正規化したroute、status、duration、`principal_id`だけです。動的なtask ID / session IDは`:task_id` / `:session_id`へ置き換え、token、Authorization header、query、raw path identifier、request / response bodyを記録しません。request workerはdisk / pipe I/Oを行わず、256件のbounded queueへnon-blockingで渡します。sink停止やqueue満杯時はrequestを止めずentryをdropし、`access_log_drops`だけをmetricsへ加算します。`/lab/metrics`も同じ固定route、method、status classのbounded aggregateで、秘密やbodyを保持しません。

### Node API

Node client が内部で使用する protocol です。手作業で token を URL やログへ露出させる必要はありません。

| メソッド | パス | 認証 | body | 用途 |
| --- | --- | --- | --- | --- |
| `POST` | `/node/v1/enroll` | `Authorization: Enroll <token>` | name, version, profile, capabilities, poll interval | Node 登録と session token 発行 |
| `POST` | `/node/v1/poll` | Node session | `{}` | last_seen 更新、active task の再配送、または次の queued task 取得 |
| `POST` | `/node/v1/tasks/{task_id}/result` | Node session | status と result | `completed` または `failed` の結果提出。同一内容の再送は冪等 |
| `POST` | `/node/v1/disconnect` | Node session | `{}` | session 失効、offline 化、未処理 task の失敗 |

Node session 要求は `Authorization: Node <session-token>` と `X-C2Lab-Node: <node-id>` の両方を使用します。

`/lab/audit` と `/lab/report` は同じmemory-only stateから作るprojectionです。新しいtaskを登録せず、Nodeへ指示せず、diskへ履歴を保存しません。

## 資源上限

| 対象 | 上限 |
| --- | ---: |
| HTTP request body | 16 KiB |
| 同時 HTTP worker | 16 |
| HTTP connection | response ごとに close |
| access log queue | 256。満杯時はrequestをblockせずdrop countを加算 |
| Operator session | 起動時3件、TTL 8時間、memory-only digest。registry上限64で、満杯時は最古のexpired / revokedだけを整理し、全件activeなら`429 session_limit` |
| Node record | 20。stale offline後60秒でsession失効。上限時は最古の`session_active: false` recordだけを自動整理 |
| 全 task | 500。到達時はrunning exerciseの参照taskを保護し、最古の整理可能なterminal taskだけを整理。対象不足なら`429 task_limit` |
| result ACK tombstone | 500。受付済みresultのretention整理後の同一再送だけに使用 |
| 1 Node の queued task | 50 |
| 1 Node の queued `RUN_PLAYBOOK` | 3 |
| 保持 exercise | 50。timelineは各16件、scenarioあたりtaskは2件 |
| queued task TTL | 既定300秒、指定時5〜86400秒 |
| `Idempotency-Key` | 8〜128文字。英数字と`-_.:` |
| `GET /lab/sync` history page | event / auditそれぞれ最大100件。短poll、長時間接続なし |
| 保持 event | 500 |
| 保持 audit entry | 500 |
| 保持 Operator note | event内に最大100件。plain textは1〜240文字、上限時は`429 note_limit` |
| UI に表示する直近 event | 100 |
| Node result | 4096 bytes |
| Node が読む HTTP response | 32 KiB |
| text / message | 240 文字 |
| poll 間隔 | 250〜3000 ms |
| `WAIT` | 0〜2000 ms |
| dispatched task deadline | 8 秒 |

## プロジェクト構成

### Key files

| 場所 | 役割 |
| --- | --- |
| [`c2lab/__main__.py`](c2lab/__main__.py) | Teamserver / Node CLI |
| [`c2lab/auth.py`](c2lab/auth.py) | memory-only Operator session、固定role / permission、token digest、失効 |
| [`c2lab/server.py`](c2lab/server.py) | loopback HTTP、認証、Operator / Node API |
| [`c2lab/observability.py`](c2lab/observability.py) | route正規化とbounded request metrics |
| [`c2lab/core.py`](c2lab/core.py) | 中央state、typed task ledger、`created_by`、cursor sync、共有note、期限監視 |
| [`c2lab/protocol.py`](c2lab/protocol.py) | capability profile、固定task、payload / result検証 |
| [`c2lab/node.py`](c2lab/node.py) | foreground poll clientと固定handler |
| [`c2lab/lab_runtime.py`](c2lab/lab_runtime.py) | Node-private一時workspaceと固定playbook |
| [`c2lab/exercises.py`](c2lab/exercises.py) | 固定ATT&CK scenario、detection metadata、containment allowlist |
| [`c2lab/static/`](c2lab/static/) | localhost Operator UI、KPI、navigation、filter |
| [`tests/`](tests/) | protocol、API、UI、安全境界のtest |
| [`.github/workflows/ci.yml`](.github/workflows/ci.yml) | Python test matrixとdashboard JavaScript構文確認 |
| [`docs/LABS.md`](docs/LABS.md) | 段階演習 |
| [`docs/PURPLE_LAB.md`](docs/PURPLE_LAB.md) | 実I/O playbookの手順と境界 |
| [`docs/DESIGN_REFERENCES.md`](docs/DESIGN_REFERENCES.md) | 設計参照、採用概念、不採用境界 |
| [`SECURITY.md`](SECURITY.md) | threat boundaryと安全方針 |

## テスト

```console
python3 -m unittest discover -s tests -v
```

GitHub ActionsのCIはpush、pull request、手動実行でPython 3.11〜3.14の`compileall`と全testを実行し、Node.js 22で`c2lab/static/app.js`の構文も確認します。外部runtime dependencyやlint packageは追加しません。

## 安全に拡張する指針

拡張は、型付きの小さな合成処理、Node-private workspace内の固定fixture処理、またはread-onlyな可視化に限定してください。

1. task 名を `protocol.py` の固定列挙へ追加する。
2. payload の必須 field、型、長さ、値域を厳密に検証する。
3. profile は新 task を動的に読み込まず、固定列挙から部分集合を選ぶ。
4. Node handler はtask ID以外の任意command/path/contentを受けず、task固有の固定schemaで検証できるJSON resultを返す。
5. task ID、correlation ID、時刻、actor、`created_by`を中央stateとeventで対応付ける。
6. 正常系、境界値、profile 拒否、timeout、安全境界の回帰テストを追加する。
7. README、SECURITY、LABS を同時に更新する。

Operator向けの可視化や共同作業機能を追加する場合も、同一lockから作るbounded projection、短いHTTP poll、厳密なcursor / size検証に限定します。共有textをNode protocolへ流用せず、audit、report、access logへ本文を複製しません。

次を必要とする案は、この教材の範囲外です。

- shell、process 起動、動的評価、任意 script
- user 指定 path、file 読書き、upload、download
- OS、process、network、user、credential の列挙
- loopback 以外の bind または controller URL
- Node の service 化、自動起動、設定や session の永続化
- payload や Node binary の生成・配布
- traffic 偽装、暗号化による秘匿、難読化、回避
- runtime plugin、hook、動的 module 読み込み

安全性の中心は、危険な能力を認証の後ろへ置くことではなく、そのコード経路を持たないことです。
