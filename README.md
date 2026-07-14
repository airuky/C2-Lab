# C2 Lab Framework

C2 Lab Framework は、中央コントローラー、Operator UI、定期的に poll する Node、非同期タスク、相関 ID、イベントログに加え、Node専用の一時workspace内で完結する限定的な実I/Oを安全に学ぶための教材です。

Node は Teamserver とは**別の実プロセス**として動きますが、同じ PC の loopback にしか接続できません。別の物理端末を操作するエージェントではなく、実際のホストを制御する C2 でもありません。

> **LOCALHOST / EPHEMERAL WORKSPACE ONLY**
>
> `purple_lab` profileの`RUN_PLAYBOOK`は、Nodeが自分で生成した一時workspaceとsynthetic fixtureだけに実際のfile I/Oを行います。Operatorはplaybook ID以外のcommand、step、path、filename、content、URL、host、argumentを指定できません。shell、subprocess、host file、file transfer、OS列挙、loopback外通信は実装されていません。

## 学べること

- Teamserver を唯一の状態管理者にする client/server の責務分離
- enrollment と通常の Node session を分ける認証ライフサイクル
- poll による `queued → dispatched → completed / failed / timeout`、待機中の `cancelled / expired` と、応答消失に備えた期限内再送
- task ID と correlation ID を使った要求、結果、イベントの対応付け
- Node profile を許可リストの部分集合として扱う capability 制御
- 固定playbookによる実I/Oと、workspace所有・結果schema・session cleanupによる境界
- 現在状態と中央イベントログを分けて観察する方法
- loopback、厳密なスキーマ、容量制限で教材の境界を保つ方法

## Architecture in 30 seconds

1. Browser Operator が固定taskを登録します。
2. Teamserver が入力を検証し、typed task ledgerとsequence付きeventを更新します。
3. 同じPCのforeground Nodeがpollし、固定処理またはNode-private workspace内の固定playbookを実行します。
4. UIはcurrent stateとauditをread-onlyに投影し、KPI、navigation、filterでlifecycleを観察します。reportは認証済みread-only APIとして取得できます。

```text
Browser Operator UI
        │ localhost HTTP + operator token
        ▼
Authoritative Teamserver
        ├── strict schema / fixed task registry
        ├── in-memory Node state / typed task ledger
        ├── FIFO task dispatch
        ├── correlation IDs
        ├── bounded sequence audit
        └── read-only report projection
                    ▲               │
        result      │               │ next task
                    │               ▼
             Foreground Node process
             loopback HTTP polling only
```

Teamserver、Node、ブラウザは別の実行主体ですが、通信先は常に `127.0.0.1` または `localhost` です。Node の `online` は「同じ PC 上の Node プロセスが最近 poll した」という意味で、リモート端末への接続を意味しません。

Operator UI は Node を作成・接続・切断しません。別ターミナルから登録された Node をread-onlyに表示し、固定taskの冪等な登録、queued taskの取消、task filter、lifecycle詳細、event / audit切替、Resetを提供します。read-only reportはAPIから取得できます。KPI cardは関連viewへのnavigationとして機能し、filterは表示だけを変えてserver stateや認可を変更しません。profile外taskと切断済みNodeはselectorで無効化され、Teamserverでも再検証されます。

## 参考にしたアーキテクチャ概念

本教材は、実運用 C2 の機能を再実装したものではありません。公式資料から、制御面の役割分離と非同期状態遷移だけを参考にしています。

- Cobalt Strike の公式ガイドは、Team Server を中央コントローラー、共有データ、ログの管理主体として説明し、複数の operator client が共有状態とイベントログを参照する構造を示しています。本教材ではこれを「中央 Teamserver と表示・操作だけを行う localhost UI」に縮小しました。参照: [Starting the Team Server](https://hstechdocs.helpsystems.com/manuals/cobaltstrike/current/userguide/content/topics/welcome_starting-cs-team-server.htm)、[Distributed and Team Operations](https://hstechdocs.helpsystems.com/manuals/cobaltstrike/current/userguide/content/topics/welcome_distributed-and-team-ops.htm)
- Sliver の公式資料は、Beacon mode を「定期的に check-in し、task を取得し、結果を後から返す」非同期モデルとして説明し、task の状態と作成・送信・完了時刻を観察できることを示しています。本教材ではこの poll/task/result の流れだけを、固定された無害な処理へ転用しました。参照: [Sliver Getting Started](https://sliver.sh/docs/?name=Getting+Started)、[Beacons vs Sessions](https://sliver.sh/tutorials/?name=2+-+Beacons+vs+Sessions)

ローカルのRamune-C2 commit `f194494` も読み取り専用で比較し、process separation、typed task ledger、structured audit / event、KPI / navigation / filterというcontrol-plane上の概念だけを独立に採用しました。参照元のcode、brand、UI asset、文言、実運用機能はコピーしていません。採用判断と不採用境界は[設計参照文書](docs/DESIGN_REFERENCES.md)に記録しています。

実 listener、payload 生成、外部 transport、任意コマンド、対話 shell、実ホスト収集、通信偽装、永続化は参考対象にも実装対象にも含めていません。Cobalt Strike、Sliver および各製品名は、それぞれの権利者に帰属します。本プロジェクトは両製品と提携していません。

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

Teamserver は `127.0.0.1:8765` だけで待ち受け、次の二種類の秘密を表示します。

- `Operator URL`: ブラウザ用の一時 operator token を `#token=...` fragment に含む URL
- `Node enrollment token`: Node を最初に登録するための一時 token

引数なしの `python3 -m c2lab` も、既定ポートで Teamserver を起動します。

### 2. Operator UI を開く

Teamserver が表示した `Operator URL` 全体を、同じ PC のブラウザで開きます。UI は fragment から token を読み取った後、アドレスバーから fragment を除去し、現在のタブの `sessionStorage` に保持します。

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

Node のターミナルで `Ctrl-C` を押すと、可能な場合は切断を通知し、Node session を失効させて終了します。その Node の未処理 task は `failed` になります。その後 Teamserver のターミナルで `Ctrl-C` を押します。Teamserver 側の Node session、task、event、token は失効します。Browser や停止していない Node process に古い token 文字列が残っていても、新しい Teamserver では使用できません。

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
python3 -m c2lab node --name NAME [--controller URL] [--profile PROFILE] [--poll-ms MS] [--enroll-token TOKEN]
```

| オプション | 既定値 | 説明 |
| --- | --- | --- |
| `--name` | 必須 | Operator UI に表示する名前。1〜48文字 |
| `--controller` | `http://127.0.0.1:8765` | loopback Teamserver URL |
| `--profile` | `training` | `basic`、`training`、`purple_lab` |
| `--poll-ms` | `1000` | poll 間隔。`250..3000` ミリ秒 |
| `--enroll-token` | prompt | 省略時は shell history に残らない非表示入力 |

controller URL は `http` と `127.0.0.1` または `localhost` だけを許可します。`localhost` も内部では `127.0.0.1` へ正規化されます。認証情報、query、fragment、追加 path を含む URL と、loopback 以外の host は起動前に拒否されます。Node client は system proxy を無効化し、HTTP redirect を追跡しません。

## 認証ライフサイクル

```text
Teamserver start
   ├─ operator token ──> Browser UI ──> Authorization: Bearer ...
   └─ enrollment token ──> Node enroll
                              └─ per-node session token
                                   ├─ poll
                                   ├─ result
                                   └─ disconnect
```

- operator token と enrollment token は、Teamserver 起動ごとに別々に生成されます。
- enrollment token は登録専用です。一回限りではなく、その Teamserver の実行中に複数 Node を登録できます。
- 登録後、Node ごとに異なる session token が発行されます。Node は以後 enrollment token ではなく、その session token と Node ID を使います。
- Node session token は Node プロセスのメモリ内だけにあり、通常は画面へ表示されません。
- 正常な disconnect は、その Node session token を直ちに失効させます。未処理の `queued` / `dispatched` task は `failed` になり、切断済み Node への新規 task は拒否されます。
- pollが途絶えてstale offlineになったsessionは60秒の回復猶予を持ちます。猶予内のpollは同じNode IDをonlineへ戻します。60秒を過ぎるとsession tokenを失効させ、未処理taskを`failed`にします。
- Reset は現在の Node と session token を破棄します。実行中 Node は、保持している enrollment token で再登録できます。
- Teamserver を再起動すると両方の起動 token が変わります。古い enrollment token を持つ Node は、新しい token で起動し直すまで登録できません。

これらは localhost 内の役割分離を学ぶための簡易認証です。同じ OS ユーザーを侵害した攻撃者に対する強固な分離ではありません。

## Node profile

profile は動的プラグインではなく、コード内に固定された許可リストの名前です。Node が登録時に申告する capabilities は、Teamserver が知る profile と完全一致しなければ拒否されます。

| profile | 許可タスク |
| --- | --- |
| `basic` | `PING`, `RUNTIME_STATUS`, `ECHO_TEXT`, `HASH_TEXT` |
| `training` | 上記に `WAIT`, `GENERATE_EVENT` を追加 |
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
| `RUN_PLAYBOOK` | `{"playbook":"DISCOVERY_FIXTURES"}` | `purple_lab`専用。Node-private一時workspaceで固定playbookを実行し、bounded evidenceを返す |

`text` と `message` は 1〜240 文字です。`GENERATE_EVENT.category` は `training`、`telemetry`、`policy`、`severity` は `info`、`warning` の固定列挙です。`RUN_PLAYBOOK`は固定playbook IDだけを受け、同一Nodeの待機playbookは3件までです。余分な field、不正な型、範囲外の値、未定義 task は登録前に拒否されます。実行方法と安全境界は[Purple Lab実挙動ガイド](docs/PURPLE_LAB.md)を参照してください。

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

各taskにはtask IDとは別にcorrelation IDが生成されます。`task.queued`、`task.dispatched`、`task.redelivered`、`task.completed`、`task.failed`、`task.timeout`、`task.cancelled`、`task.expired`、`task.pruned` のeventが同じcorrelation IDを持つため、一つの要求を時系列で追跡できます。各eventの `sequence` はTeamserver process内の順序を安定して比較するための値です。Reset後もcounterは進みますがretained eventは `lab.reset` から始まり、Teamserver restartではcounterを含む全memory stateが失われます。

Operatorのtask登録は任意の`Idempotency-Key`を受けます。8〜128文字の英数字と`-_.:`だけを許可し、retained memory state内で同じNode、type、payload、queue TTLの再送を同じtaskへ収束させます。同じkeyを異なるrequestへ使うと`409 idempotency_conflict`です。UIは通信結果が不明な再試行で同じkeyを再利用し、正常受付または確定した4xx後に破棄します。key自体はtask、event、audit、reportへ公開しません。

Node は実行済み result を acknowledgement が返るまで memory 内の pending-result outbox に保持し、新しい task を poll する前に同じ result を再送します。Teamserver に届く前に request が失われた場合は再送で登録され、登録後の HTTP response だけが失われた場合も、同じ status と result の再送は冪等な成功になります。異なる内容の再送は `409 result_conflict` です。Node process を終了すると outbox も失われるため、永続配送を保証する仕組みではありません。

初回resultがtask固有contractに一致しない場合、taskは`dispatched`のまま確定せず、`task.result_rejected`をevent/auditへ記録します。記録するのはtask typeと固定reasonだけで、不正resultの内容はevent/auditへコピーしません。

Node は poll のたびに `last_seen` を更新します。正常な切断通知がない場合は、最後の poll から `max(8秒, poll間隔×3)` 後に `offline` と判定されます。このstale offlineは60秒間だけsessionが有効な回復可能状態です。期限内にpollすればonlineへ戻り、期限を過ぎると`node.session_expired`を記録してsessionを失効させ、未処理taskを`failed`にします。実行中Nodeは`401`を受けると保持中のenrollment tokenで新しいNodeとして再登録します。

| `status` | `session_active` | 意味 | 新規 task |
| --- | --- | --- | --- |
| `online` | `true` | 最近 poll した | 受付可能 |
| `offline` | `true` | stale後60秒以内。poll再開可能 | queuedとして受付可能 |
| `offline` | `false` | disconnectまたはstale session TTL失効済み | `409 node_disconnected` |

## API

すべての API は loopback からの要求と localhost の `Host` header だけを受け付けます。`POST` body は `application/json` です。HTTP response ごとに `Connection: close` を返し、idle keep-alive を保持しません。

### Operator API

`/healthz` を除き、`Authorization: Bearer <operator-token>` が必要です。ブラウザが `Origin` を送る書込要求では、同じ localhost origin だけを許可します。

| メソッド | パス | body | 用途 |
| --- | --- | --- | --- |
| `GET` | `/healthz` | — | 認証不要の生存確認 |
| `GET` | `/lab/overview` | — | 集計、Node、task、event のスナップショット |
| `GET` | `/lab/nodes` | — | Node 一覧 |
| `GET` | `/lab/tasks` | — | task 一覧 |
| `GET` | `/lab/events` | — | 中央イベント一覧 |
| `GET` | `/lab/audit` | — | sequence順のbounded structured audit view |
| `GET` | `/lab/report` | — | Nodeとtask lifecycleのread-only集計 |
| `POST` | `/lab/tasks` | node ID、type、payload、任意queue TTL | 固定taskを登録。任意`Idempotency-Key`対応 |
| `POST` | `/lab/tasks/{task_id}/cancel` | `{}` | queued taskだけを取消。cancelledへの再送は冪等 |
| `POST` | `/lab/reset` | `{}` | Node session、task、eventを初期化し、auditへResetを記録 |

`POST /lab/tasks`の任意`queue_ttl_seconds`は5〜86400の整数で、省略時は300です。`Idempotency-Key`はHTTP headerで渡し、同じkeyを異なるtask requestへ再利用すると`409`です。取消済みtaskの再取消は同じ`cancelled` recordを返しますが、dispatchedまたは他のterminal taskは`409 task_not_cancellable`です。

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
| Node record | 20。stale offline後60秒でsession失効。上限時は最古の`session_active: false` recordだけを自動整理 |
| 全 task | 500。到達時は最古のterminal taskだけを整理。全件non-terminalなら`429 task_limit` |
| 1 Node の queued task | 50 |
| 1 Node の queued `RUN_PLAYBOOK` | 3 |
| queued task TTL | 既定300秒、指定時5〜86400秒 |
| `Idempotency-Key` | 8〜128文字。英数字と`-_.:` |
| 保持 event | 500 |
| 保持 audit entry | 500 |
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
| [`c2lab/server.py`](c2lab/server.py) | loopback HTTP、認証、Operator / Node API |
| [`c2lab/core.py`](c2lab/core.py) | 中央state、typed task ledger、event sequence、期限監視 |
| [`c2lab/protocol.py`](c2lab/protocol.py) | capability profile、固定task、payload / result検証 |
| [`c2lab/node.py`](c2lab/node.py) | foreground poll clientと固定handler |
| [`c2lab/lab_runtime.py`](c2lab/lab_runtime.py) | Node-private一時workspaceと固定playbook |
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
5. task ID、correlation ID、時刻、actor を中央 event に残す。
6. 正常系、境界値、profile 拒否、timeout、安全境界の回帰テストを追加する。
7. README、SECURITY、LABS を同時に更新する。

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
