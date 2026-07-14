# C2 Lab Framework 演習ガイド

このガイドでは、別 process の Teamserver、Browser Operator、foreground Node を使い、中央状態管理、enrollment、poll、非同期 task、correlation ID、event log を段階的に学びます。

Node は別の実 process ですが、同じ PC の loopback にしか接続できません。演習中の「別ターミナル」は、別の物理 PC ではなく同じ PC 上の terminal window を意味します。

## 演習ルール

- 実在する人、組織、端末、IP address、credential を名前や payload に使わない。
- Operator URL、operator token、enrollment token、認証 header を記録・共有しない。
- Teamserver を port forward、proxy、tunnel、container 公開 port で外部公開しない。
- sourceを変更する演習でも、shell、workspace外のhost file、OS列挙、外部通信、永続化、回避を追加しない。
- 各演習の開始条件を揃えるため、必要に応じて Node を停止してから Reset する。

Python 3.11 以降を使用します。

## 観察する三つの層

| 層 | 主な表示 | 問い |
| --- | --- | --- |
| Current state | Overview、Nodes、Tasks | 今どうなっているか |
| Event history | Events | 何が、いつ、誰によって変わったか |
| Process output | Teamserver / Node terminal | どの process が何を認識したか |

task を観察するときは、最低でも次を記録します。

| 項目 | 値 |
| --- | --- |
| task ID |  |
| correlation ID |  |
| Node ID |  |
| type |  |
| created |  |
| dispatched |  |
| completed |  |
| final status |  |

## Lab 0: CLI と安全境界

### 目的

起動前に、利用できる process と固定機能を確認します。

### 手順

1. repository root で test を実行します。

   ```console
   python3 -m unittest discover -s tests -v
   ```

2. CLI help を確認します。

   ```console
   python3 -m c2lab --help
   python3 -m c2lab teamserver --help
   python3 -m c2lab node --help
   ```

3. Node の `--controller`、`--profile`、`--poll-ms` の選択肢を記録します。
4. `c2lab/protocol.py` で task と profile が固定列挙であることを確認します。
5. `c2lab/node.py`で、通常handlerは入力JSONだけを処理し、`purple_lab`も固定synthetic registry以外へ到達せず、shell、host file、OS列挙を行わないことを確認します。

### 確認問題

- Node が別 process であることと、別の物理端末を制御できることはなぜ同じではありませんか。
- bind address を CLI から指定できないことは、どの安全境界を守りますか。
- profile が plugin ではなく固定 task の部分集合である利点は何ですか。

### 完了条件

Teamserver と Node の役割、および「実装されていない能力」を自分の言葉で説明できること。

## Lab 1: Teamserver と Operator token

### 目的

Teamserver 起動時の秘密分離と、Browser Operator の認証を確認します。

### 手順

1. 一つ目のターミナルで Teamserver を起動します。

   ```console
   python3 -m c2lab teamserver --port 8765
   ```

2. 出力に `Operator URL` と `Node enrollment token` が別々にあることを確認します。値そのものは課題メモへ記録しません。
3. Browser の別 tab で `http://127.0.0.1:8765/healthz` を開き、認証なしで lab mode と protocol を示す JSON が返ることを確認します。

4. `Operator URL` 全体を同じ PC の browser で開きます。
5. UI 接続後、address bar から `#token=...` が除去されたことを確認します。
6. token を UI から消去し、lab state を読めなくなることを確認します。元の Operator URL を terminal からもう一度開いて復帰します。

### 観察課題

- `/healthz` は取得できても `/lab/overview` は token なしで取得できません。この差は何のためですか。
- URL fragment が通常の HTTP request に含まれない利点は何ですか。
- operator token と enrollment token を一つにしない理由を考えてください。

### 完了条件

Operator UI が token をどこから取得し、どの API に使うかを説明できること。

## Lab 2: Node enrollment と per-node session

### 目的

共有 enrollment token から Node 固有 session へ移る流れを観察します。

### 手順

1. 二つ目のターミナルを同じ PC 上で開きます。
2. token option を省略して Node を起動し、prompt に enrollment token を入力します。

   ```console
   python3 -m c2lab node \
     --name lab-node-01 \
     --controller http://127.0.0.1:8765 \
     --profile training \
     --poll-ms 1000
   ```

3. Node terminal に `Enrolled as node-...` が表示されることを確認します。
4. Operator UI の Nodes で、次を確認します。

   - status
   - active Node に `SESSION CLOSED` badge がないこと
   - version
   - profile
   - transport が `loopback-http-poll/v1`
   - poll interval
   - last seen
   - completed / failed count
   - capabilities

5. Events で `node.enrolled` を探し、Node ID、profile、transport を確認します。
6. Browser Network panel を使う場合は、Node API ではなく Operator UI の request だけを観察します。token を HAR file や screenshot に保存しません。

### 観察課題

- enrollment token は複数 Node の登録に使えますが、登録後の poll に使われないのはなぜですか。
- per-node session token と Node ID の組合せにはどんな意味がありますか。
- Node session token が terminal に表示されない理由を考えてください。

### 完了条件

`enrollment → Node ID + session token → poll` の順序を説明できること。

## Lab 3: Poll、stale、disconnect

### 目的

Node の `status` と session の有効性が別の軸であることを確認します。

### 手順

1. `lab-node-01` の last seen を数回更新し、poll interval と比較します。
2. Events の `node.heartbeat` を探します。heartbeat event は毎 poll ではなく、一定間隔で記録されます。
3. Node terminal で `Ctrl-C` を押し、正常終了します。
4. UI を更新し、`node.disconnected` event、`status: offline`、`session_active: false` を確認します。
5. 切断済み Node に `SESSION CLOSED` badge が付き、task の送信先として選べないことを確認します。server 側の `409 node_disconnected` は `test_disconnect_marks_node_offline` で確認します。
6. 別名 `lab-node-02` で Node を再起動します。同じ enrollment token を使用できます。
7. 新しい Node ID と session が発行され、以前の Node record と別 identity になることを確認します。

```console
python3 -m unittest tests.test_core.LabStateTests.test_disconnect_marks_node_offline -v
```

### 追加観察

Node terminal が正常 disconnect を送らず poll だけ途絶えた場合、Teamserver は最終 poll から `max(8秒, poll間隔×3)` 後に `node.stale` を記録します。その後60秒は`status: offline`でも`session_active: true`であり、同じprocessのpollが再開すると`node.online`を記録して回復できます。60秒を過ぎると`node.session_expired`を記録してsessionを失効させ、未処理taskを`failed`にします。

stale と回復は、強制終了を手作業で再現する代わりに deterministic test で確認できます。

```console
python3 -m unittest tests.test_core.LabStateTests.test_node_becomes_stale_and_poll_recovers_it -v
python3 -m unittest tests.test_core.LabStateTests.test_stale_session_expires_after_ttl_and_fails_queued_tasks -v
```

Node record の上限は 20 件です。stale sessionも60秒後にはclosedとなるため、21件目の登録時に最古のclosed recordだけを自動整理して`node.pruned`を記録できます。全recordがactiveなら`429 node_limit`です。次のtestで、active sessionを捨てずにclosed historyだけを整理することを確認できます。

```console
python3 -m unittest tests.test_core.LabStateTests.test_oldest_closed_node_record_is_pruned_at_limit -v
python3 -m unittest tests.test_core.LabStateTests.test_expired_stale_sessions_release_node_capacity -v
```

### 確認問題

- last seen、heartbeat event、status、session active はそれぞれ何を表しますか。
- Node process 再起動後に同じ ID を再利用しないことは、event の解釈にどう役立ちますか。
- stale後60秒以内と、disconnectまたはTTL失効後では、旧Node IDへtaskを登録した結果がどう異なりますか。

### 完了条件

回復可能なstale sessionと、disconnect / TTLで閉じたsessionを区別できること。

## Lab 4: PING と task lifecycle

### 目的

task の受付、配送、結果を task ID と correlation ID で追跡します。

### 手順

1. online の `training` Node を一つ用意します。
2. Operator UI で `PING`、payload `{}` を登録します。
3. Task table と詳細で次を確認します。

   - task ID
   - correlation ID
   - Node ID / name
   - `created_at`
   - `dispatched_at`
   - `completed_at`
   - `delivery_attempts`
   - payload / result

4. Events から同じ task ID の `task.queued`、`task.dispatched`、`task.completed` を探します。通常の一往復なら `delivery_attempts` は 1 です。
5. 三つの event data に同じ correlation ID があることを確認します。
6. `created → dispatched` と `dispatched → completed` の時間を計算します。

### 記録表

| 区間 | おおよその時間 | 主に影響するもの |
| --- | ---: | --- |
| created → dispatched |  | poll interval |
| dispatched → completed |  | Node handler と次の HTTP request |
| created → completed |  | 上記の合計 |

### 確認問題

- task ID と correlation ID を別に持つ利点は何ですか。
- Operator API が `queued` を返した時点で、Node は task を知っていますか。
- current task row だけでは分からず、event を見ると分かることを挙げてください。

### 完了条件

一つの task を三つの process view と三つの lifecycle event で対応付けられること。

## Lab 5: 固定された安全な処理

### 目的

Node が実 host ではなく、与えられた合成 input と Node runtime 自体の限定情報だけを扱うことを確認します。

### 手順

1. `RUNTIME_STATUS` を実行します。
2. result が version、profile、uptime、完了 task 数、poll interval に限定され、OS user、process list、network、file path を含まないことを確認します。
3. `ECHO_TEXT` に `hello-lab` を渡し、同じ text が返ることを確認します。
4. `HASH_TEXT` に同じ `hello-lab` を二回渡し、同じ SHA-256 digest になることを確認します。
5. 異なる text で `HASH_TEXT` を実行し、digest が変わることを確認します。
6. `GENERATE_EVENT` へ次の合成 payload を渡します。

   ```json
   {
     "category": "training",
     "severity": "info",
     "message": "exercise event"
   }
   ```

7. task lifecycle event とは別に `node.generated_event` が追加されることを確認します。

### 確認問題

- `HASH_TEXT` が安全なのは、何を hash し、何を読まないからですか。
- `RUNTIME_STATUS` と OS inventory の違いは何ですか。
- generated event と task completed event は、task ID を使ってどう関連付けられますか。

### 完了条件

六つの task が任意 command ではなく、固定 schema の小さな処理であることを説明できること。

## Lab 6: Profile と capability 拒否

### 目的

Node profile が許可 task の部分集合であり、Node と Teamserver の両方で検証されることを確認します。

### 手順

1. 三つ目のターミナルで `basic` Node を起動します。

   ```console
   python3 -m c2lab node --name basic-node --profile basic
   ```

2. prompt に enrollment token を入力します。
3. UI の capabilities が `PING`、`RUNTIME_STATUS`、`ECHO_TEXT`、`HASH_TEXT` だけであることを確認します。
4. `basic-node` を選ぶと `WAIT` と `GENERATE_EVENT` が selector で無効になることを確認します。
5. Teamserver 側でも profile 外 task が `capability_denied` になることを test で確認します。

   ```console
   python3 -m unittest tests.test_core.LabStateTests.test_profile_capabilities_are_enforced -v
   ```

6. `training` Node では `WAIT` を選択でき、完了することを確認します。
7. payload に余分な field を加え、UI の local validation で拒否されることを確認します。Teamserver にも同じ strict validation があることを test で確認します。

### 確認問題

- UI の task selector に表示されることと、選択した Node が実行を許可されることはなぜ別ですか。
- Node が capabilities を自由に追加できない仕組みを code から確認してください。
- profile を runtime plugin にしないことは、どの安全境界を守りますか。

### 完了条件

全体 task registry、profile subset、Node handler の三段階を区別できること。

## Lab 7: FIFO queue と WAIT

### 目的

poll 間隔、queue 待ち時間、一 Node 一 task の関係を観察します。

### 手順

1. `training` Node を `--poll-ms 3000` で起動します。
2. UI から続けて次の三 task を登録します。

   1. `WAIT` with `{"milliseconds":2000}`
   2. `PING` with `{}`
   3. `ECHO_TEXT` with `{"text":"after-wait"}`

3. Tasks で、一件だけが `dispatched` になり、残りが `queued` で待つことを確認します。
4. 全 task の完了後、created、dispatched、completed の時刻を比較します。
5. Events を correlation ID ごとに分け、各 task の順序を確認します。

### 記録表

| type | created | dispatched | completed | queue wait | processing |
| --- | --- | --- | --- | ---: | ---: |
| `WAIT` |  |  |  |  |  |
| `PING` |  |  |  |  |  |
| `ECHO_TEXT` |  |  |  |  |  |

### 確認問題

- `WAIT` の後ろの短い task が先に完了しないのはなぜですか。
- poll interval と `WAIT` は、task lifecycle のどの区間へ影響しますか。
- 複数 Node へ一件ずつ task を登録した場合、それらは直列ですか、独立ですか。

### 完了条件

queue wait と Node processing time を別々に説明できること。

## Lab 8: queued、cancelled、expired、failed、timeout の違い

### 目的

task の最終状態を、配送済みか、queue TTL内か、Operatorが取り消したか、Node sessionが有効かから判断します。

### queued と stale session

stale offline は正常 disconnect と異なり、最初の60秒はsessionがまだ有効です。`test_node_becomes_stale_and_poll_recovers_it` と `LabState.queue_task` / `poll_node` を読み、次を確認します。

1. poll が途絶えると status は offline になる。
2. 60秒の回復猶予中はsession activeがtrueのままである。
3. 新規 task は拒否されず `queued` になる。
4. 同じ Node session が poll を再開すると online に戻り、task を取得できる。
5. 回復せず60秒を過ぎるとsessionが失効し、残るqueued taskは`failed`になる。

### queuedの取消と期限切れ

queued taskは既定300秒のqueue TTLを持ち、Operator APIでは5〜86400秒を指定できます。UIのtask詳細からqueued taskだけを取り消せます。`cancelled`と`expired`はNodeへ配送されず、dispatch後のtaskは取消やqueue期限切れの対象になりません。

```console
python3 -m unittest tests.test_core.LabStateTests.test_queued_task_expires_but_dispatched_task_does_not -v
python3 -m unittest tests.test_core.LabStateTests.test_queued_task_cancellation_is_idempotent_and_never_dispatches -v
```

### disconnect による failed

1. `--poll-ms 3000` の Node を起動します。
2. poll の直後に `PING` を登録し、次の poll より前に Node terminalで `Ctrl-C` を押します。
3. 正常 disconnect により、まだ queued だった task が `failed` になることを確認します。
4. `task.failed` event の `reason` が `node_disconnected` であることを確認します。
5. 同じ Node ID が UI で選択不能になることを確認します。server 側の `409 node_disconnected` は `test_disconnect_marks_node_offline` で確認します。

Node handler 自身の fail-closed も test で確認できます。

```console
python3 -m unittest tests.test_node.NodeExecutorTests.test_command_shaped_or_malformed_tasks_fail_closed -v
```

### timeout の確認

正常 disconnect は未処理 task を直ちに `failed` にするため、`Ctrl-C` では timeout を再現しません。deadline の挙動は、時刻を注入する deterministic test で確認します。

```console
python3 -m unittest tests.test_core.LabStateTests.test_dispatched_task_times_out_and_rejects_late_result -v
```

test から、配送後 8 秒で `timeout` になり、遅い result が `409` で拒否されることを読み取ります。

### 通信エラーと応答消失

この教材は、HTTP request と response のどちらが失われたかを完全には判別できない、という非同期処理の基本も観察できます。

1. poll response が Node に届かなかった場合、Teamserver 上では task が `dispatched` のままです。同じ session の次回 poll には期限を変えず同じ task を返し、`delivery_attempts` と `task.redelivered` を増やします。
2. Node が task を実行した後、result request が Teamserver へ届かなければ、Node は pending result を memory outbox に保持して再送します。acknowledgement を得るまでは新しい task を poll しません。
3. Teamserver が result を確定した後、その HTTP response だけが失われた場合、同じ status と result の再送は冪等に成功します。completed / failed counter と lifecycle event は一度だけ増えます。
4. 同じ task ID へ異なる status または result を再送すると `409 result_conflict` です。
5. 再配送や result 再送は最初の 8 秒 deadline を延長せず、Node process を終了すると memory outbox も失われます。

Operatorのtask登録responseが失われた場合は別の冪等性を使います。UIは同じrequestの再試行に同じ`Idempotency-Key`を再利用し、Teamserverは同じretained taskを返します。同じkeyを異なるNode、type、payload、queue TTLへ使うと`409 idempotency_conflict`です。

Teamserver 側の再配送と冪等性は、次の deterministic test で確認します。

```console
python3 -m unittest tests.test_core.LabStateTests.test_only_one_task_is_dispatched_per_node -v
python3 -m unittest tests.test_core.LabStateTests.test_identical_result_retry_is_idempotent -v
python3 -m unittest tests.test_core.LabStateTests.test_task_creation_idempotency_prevents_duplicate_queue_entries -v
python3 -m unittest tests.test_node.NodeClientStateTests.test_result_outbox_retries_same_result_after_connection_loss -v
```

### 比較

| 状態 | Node へ配送済み | session | 意味 |
| --- | --- | --- | --- |
| `queued` | いいえ | active | 次回 poll を待っている |
| `cancelled` | いいえ | どちらもあり得る | Operatorが配送前に取り消した |
| `expired` | いいえ | どちらもあり得る | queue TTLまでに配送されなかった |
| `failed` | どちらもあり得る | closed または active | disconnect で閉じられたか、Node が安全な失敗 result を返した |
| `timeout` | はい | result なし | 再配送・再送を含め、最初の deadline までに result が確定しなかった |

### 完了条件

queue TTL、cancel、stale session TTL、disconnect、handler failure、通信エラー、二種類の冪等再送、deadlineの違いからfinal statusを説明できること。

## Lab 9: Reset と token 失効

### 目的

Reset、Teamserver restart、Node restart の違いを確認します。

### 手順

1. すべての Node を停止します。
2. Node、task、event の件数を記録してから UI の Reset を実行します。
3. Reset 確認画面が Node session の失効を明示することを確認します。
4. Overview が初期化され、Events に `lab.reset` だけが残ることを確認します。
5. Teamserver は停止せず、以前と同じ enrollment token で Node を起動します。登録できることを確認します。
6. Node を動かしたまま、もう一度 Reset します。
7. Node は旧 session で `401` を受けた後、保持している enrollment token で再登録し、新しい Node ID を得ることを確認します。
8. Teamserver を停止して再起動します。Operator URL と enrollment token が変わることを確認します。
9. 古い token を持つ UI と Node が認証できないことを確認し、Node を停止して新しい enrollment token で起動し直します。

### 比較表

| 操作 | Node/task/event | per-node session | operator/enrollment token |
| --- | --- | --- | --- |
| Node restart | 旧recordはstale後60秒でclosedになる | 旧sessionはTTL失効し、新Nodeは新session | 変わらない |
| Reset | 消える | すべて無効 | 変わらない |
| Teamserver restart | 消える | すべて無効 | 両方更新 |

### 完了条件

state reset と起動 token rotation を区別できること。

## Lab 10: 中央 event log と read-only report

### 目的

current state、event history、そこから計算する report の違いを整理します。

### 手順

1. 二つの Node を登録し、それぞれへ複数 task を実行します。
2. Events を actor で分類します。

   - `operator`: task受付、queued taskの取消、Reset
   - `node`: enrollment、poll による配送・再配送、結果、disconnect event
   - `teamserver`: disconnect時の未処理task cleanup、timeout、queue/session期限、closed Node / terminal taskの整理

3. correlation ID ごとに lifecycle event をまとめます。
4. task data から次の値を計算します。

   - Node 別 completed / failed / timeout / cancelled / expired 件数
   - created から dispatched までの平均待ち時間
   - dispatched から completed までの平均処理時間
   - task type 別の件数

5. 計算結果は event と state の read-only な派生物であり、元の task status を変更しないことを確認します。

### 確認問題

- Teamserver に event を集約すると、Browser を閉じている間の Node 活動も観察できるのはなぜですか。
- event 上限 500 は、監査保証と教材の資源制限のどちらを優先した設計ですか。
- task上限500で最古terminalだけを`task.pruned`にし、queued / dispatchedを残す理由は何ですか。
- report を生成する処理に task 登録権限が不要なのはなぜですか。

### 完了条件

state store、event log、report projection の責務を分けて説明できること。

## Lab 11: 参考アーキテクチャと安全な差異

### 目的

実製品の公式資料から抽象概念だけを取り出し、安全境界を保って教材へ置き換える方法を学びます。

### 公式資料

- [Cobalt Strike: Starting the Team Server](https://hstechdocs.helpsystems.com/manuals/cobaltstrike/current/userguide/content/topics/welcome_starting-cs-team-server.htm)
- [Cobalt Strike: Distributed and Team Operations](https://hstechdocs.helpsystems.com/manuals/cobaltstrike/current/userguide/content/topics/welcome_distributed-and-team-ops.htm)
- [Sliver: Getting Started](https://sliver.sh/docs/?name=Getting+Started)
- [Sliver: Beacons vs Sessions](https://sliver.sh/tutorials/?name=2+-+Beacons+vs+Sessions)

### 対応表

| 参考概念 | 本教材 | 意図的に除外したもの |
| --- | --- | --- |
| 中央 Team Server | memory-only localhost Teamserver | 外部 bind、実対象の data、永続 log |
| operator client | Browser Operator UI | remote multi-user service、実 command console |
| beacon check-in | foreground Node poll | implant、background service、外部 callback |
| task / result | 固定JSON task、task固有result、purple_labのbounded evidence | shell、workspace外のhost file、任意OS operation |
| task history | correlation ID と central event | tamper-proof 永続監査 |

### 設計課題

`COUNT_TEXT` という新しい合成 task を、実装せず設計だけしてください。1〜240文字の `text` を受け取り、文字数だけを返すものとします。

次を定義します。

1. exact payload schema
2. result schema と最大 size
3. `basic` / `training` のどちらに含めるか、その理由
4. queued、dispatched、redelivered、completed、failed、timeout、cancelled、expired event と `delivery_attempts`
5. 空文字、最大長、余分な field、不正な型の test
6. Node handler が input 以外へアクセスしないことの review 項目
7. UI が input / result を text として表示する test

### 安全 review checklist

- [ ] task 名が固定列挙である
- [ ] payload field が完全一致である
- [ ] length、range、result size に上限がある
- [ ] profile が固定 registry の部分集合である
- [ ] handlerは渡されたJSON、または固定synthetic registryだけを使う
- [ ] process、shell、workspace外のfile、OS metadataに触れない
- [ ] loopback 以外へ request しない
- [ ] session、task、result を disk / database へ永続化しない
- [ ] task と event に correlation ID がある
- [ ] pending result は memory 内だけに保持し、同一再送は冪等である
- [ ] UI と server の両方に拒否 test がある
- [ ] README、SECURITY、LABS を更新する

### 完了条件

「参考にした制御面」と「実装してはいけない実運用能力」の境界を説明できること。

## Lab 12: purple_lab固定playbook

### 目的

別プロセスNodeがNode-private一時workspace内で実際のfile I/Oを行い、task lifecycleとbounded evidenceへ対応付ける方法を学びます。実target操作との境界も確認します。

### 手順

1. `purple_lab` Nodeを起動します。

   ```console
   python3 -m c2lab node --name purple-node-01 --profile purple_lab
   ```

2. UIでcapabilityに`RUN_PLAYBOOK`があることを確認します。
3. `DISCOVERY_FIXTURES`、`COLLECT_AND_STAGE`、`CREATE_CANARY`、`CLEANUP`を順に登録します。
4. task ID、correlation ID、event sequence、audit actionを対応付けます。
5. resultの`scope`、`attack_techniques`、`steps`、`evidence`を比較します。
6. resultにabsolute path、raw fixture、host metadata、tokenがないことを確認します。
7. `training` Nodeでは`RUN_PLAYBOOK`がcapability deniedになることを確認します。
8. unknown playbookと余分な`path`、`command`、`steps` fieldがtestで拒否されることを確認します。
9. Teamserver Reset後にNodeが再登録し、旧workspace artifactを引き継がないことを確認します。

### 確認問題

- 実際のfile create/read/hash/deleteと、実targetへのpost-exploitationは何が違いますか。
- `CLEANUP`とNode終了・session失効によるworkspace cleanupは何が違いますか。
- ATT&CK mappingが実targetでのtechnique実行や検知coverageを証明しないのはなぜですか。
- payloadにpathやcontentを持たせないことは、どの転用経路を閉じますか。

### 完了条件

`purple_lab`が実挙動である一方、remote C2へ転用できるinterfaceを持たない根拠を、payload schema、workspace ownership、result schema、network boundaryの四点から説明できること。

## まとめ課題

次の問いへ、Current state、Events、terminal output、source/test のうち二種類以上を根拠として答えてください。

1. Operator が task を登録してから Node result が表示されるまで、どの process と状態を通りますか。
2. enrollment token と per-node session token は、用途と失効条件がどう異なりますか。
3. stale後60秒以内、stale session TTL失効後、正常disconnect後で、新規taskと未処理taskの扱いはどう異なりますか。
4. correlation ID は task ID だけでは解きにくい何を説明しますか。
5. `RUNTIME_STATUS` と `HASH_TEXT` が実 host の列挙や file hash にならない根拠は何ですか。
6. Cobalt Strike と Sliver から取り入れた概念、意図的に除外した能力をそれぞれ挙げてください。
7. Node が別 process でも、本教材が別の物理端末を制御できない理由を code と CLI から説明してください。
8. poll response と result response が失われた場合、再配送、outbox、冪等受付がそれぞれ何を回復し、何を保証しないか説明してください。
9. `purple_lab`の実I/Oがworkspace外へ到達せず、session失効時にartifactを引き継がない根拠を説明してください。
10. queue TTL、cancel、`Idempotency-Key`、terminal-only retentionがNode capabilityを増やさない理由を説明してください。

すべて説明できれば、中央 Teamserver、Operator、foreground Node、poll、task/result、correlation、event log、安全境界という本教材の主要概念を一通り確認できています。
