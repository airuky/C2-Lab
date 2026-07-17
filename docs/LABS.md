# C2 Lab Framework 演習ガイド

このガイドでは、別 process の Teamserver、固定roleのBrowser Operator、foreground Node を使い、中央状態管理、Operator session、enrollment、poll、非同期 task、correlation ID、cursor差分同期、Operator共有メモ、bounded observabilityを段階的に学びます。

Node は別の実 process ですが、同じ PC の loopback にしか接続できません。演習中の「別ターミナル」は、別の物理 PC ではなく同じ PC 上の terminal window を意味します。

## 演習ルール

- 実在する人、組織、端末、IP address、credential を名前や payload に使わない。
- Operator共有メモにもtoken、実在する対象、credential、個人情報を書かない。
- Admin / Operator / Viewer URL、各Operator token、enrollment token、認証 header を記録・共有しない。
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

## Lab 1: Teamserver、Operator session、固定RBAC

### 目的

Teamserver 起動時の秘密分離、8時間のmemory-only Operator session、Browser UIとserver-side RBACを確認します。

### 手順

1. 一つ目のターミナルで Teamserver を起動します。

   ```console
   python3 -m c2lab teamserver --port 8765
   ```

2. 出力に`Admin URL`、`Operator URL`、`Viewer URL`と`Node enrollment token`が別々にあることを確認します。Operator sessionはすべて8時間で期限切れになり、refresh APIはなく、継続時はTeamserver再起動で新URLが必要という説明も確認します。値そのものは課題メモへ記録しません。
3. Browser の別 tab で次を開き、どちらも認証不要であることを確認します。

   - `http://127.0.0.1:8765/healthz`: processの固定liveness。runtime状態を判定しない
   - `http://127.0.0.1:8765/readyz`: runtime monitorのreadiness。通常起動後は`200 ready`、未準備・異常時は`503 not_ready`

4. `Admin URL` 全体を同じ PC のbrowserで開きます。
5. UI 接続後、address bar から `#token=...` が除去されたことと、principal `local-admin`、role `admin`が表示されることを確認します。UIが読む`GET /lab/session`にはtokenが含まれません。
6. `Operator URL`と`Viewer URL`も別のtabで開き、次の固定permissionを比較します。

   | role | permissions |
   | --- | --- |
   | `admin` | `read`, `task_write`, `exercise_write`, `containment_write`, `note_write`, `reset`, `operator_admin` |
   | `operator` | `read`, `task_write`, `exercise_write`, `note_write` |
   | `viewer` | `read` |

7. viewerではtask登録・取消、共有メモ投稿、Resetが無効になり、operatorでは共有メモを投稿できる一方でResetが無効になることを確認します。これは補助表示であり、serverも有効sessionのpermission不足を`403 Forbidden`で拒否します。
8. tokenをUIから消去し、lab stateを読めず`401 Unauthorized`になることを確認します。元のrole URLをterminal出力からもう一度開いて復帰します。
9. admin-onlyのtoken-free session一覧と個別revoke、expiry、401 / 403は次のtestでも確認します。token値はtest出力へ記録されません。

   ```console
   python3 -m unittest \
     tests.test_server.OperatorRBACServerTests.test_session_identifies_principal_and_expired_session_is_unauthorized \
     tests.test_server.OperatorRBACServerTests.test_operator_admin_routes_are_admin_only_and_revoke_immediately \
     tests.test_server.OperatorRBACServerTests.test_role_permissions_enforce_read_task_write_and_reset -v
   ```

### 観察課題

- `/healthz`と`/readyz`はどの状態を別々に答え、なぜ`/lab/overview`はtokenなしで取得できないのですか。
- URL fragment が通常の HTTP request に含まれない利点は何ですか。
- raw Operator tokenではなくdigestだけをTeamserver memoryへ保存する理由は何ですか。
- viewerのUI controlを隠すだけでなく、Teamserverが403を返す必要があるのはなぜですか。
- Operator token と enrollment token を一つにしない理由を考えてください。

### 完了条件

三つのOperator session URL、固定permission、`/lab/session`、401 / 403、digest保存を対応付けて説明できること。

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

`SIGTERM`は`Ctrl-C`と同じgraceful shutdown経路へ変換されます。Nodeは可能ならdisconnectとworkspace cleanupを行い、TeamserverはHTTP serverとruntime monitorを閉じます。signal handlerの接続は次のtestで確認できます。`SIGKILL`やOS crashではcleanupを保証しません。

```console
python3 -m unittest tests.test_main.SigtermContextTests tests.test_main.MainSignalIntegrationTests -v
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
   - `created_by`
   - payload / result

4. `created_by`がtaskを登録したtabのprincipalであり、Eventsの`task.queued.actor`と一致することを確認します。
5. Events から同じ task ID の `task.queued`、`task.dispatched`、`task.completed` を探します。通常の一往復なら `delivery_attempts` は 1 です。
6. 三つの event data に同じ correlation ID があることを確認します。
7. `created → dispatched` と `dispatched → completed` の時間を計算します。

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
- `created_by`をBrowser bodyから受けず、認証済みsessionから決める理由は何ですか。

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

Operatorのtask登録responseが失われた場合は別の冪等性を使います。UIは同じrequestの再試行に同じ`Idempotency-Key`を再利用し、Teamserverは同じretained taskを返します。同じkeyを別actorまたは異なるNode、type、payload、queue TTLへ使うと`409 idempotency_conflict`です。

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

1. すべての Node を停止し、AdminまたはOperator tabから`reset exercise note`という共有メモを一件投稿します。
2. Node、task、event、`operator.note` の件数を記録してから UI の Reset を実行します。
3. Reset 確認画面が Node session の失効を明示することを確認します。
4. Overview が初期化され、共有noteを含むretained Eventsが消え、Events に `lab.reset` だけが残ることを確認します。AuditにはResetが残り、note本文は複製されていないことも確認します。
5. Teamserver は停止せず、以前と同じ enrollment token で Node を起動します。登録できることを確認します。
6. Node を動かしたまま、もう一度 Reset します。
7. Node は旧 session で `401` を受けた後、保持している enrollment token で再登録し、新しい Node ID を得ることを確認します。
8. Teamserver を停止して再起動します。Admin / Operator / Viewer URLとenrollment tokenがすべて変わることを確認します。
9. 古いOperator tokenを持つUIと古いenrollment tokenを持つNodeが認証できないことを確認し、Admin URLを開き直してからNodeを新しいenrollment tokenで起動し直します。

### 比較表

| 操作 | Node/task/event/note | per-node session | Operator session / enrollment token |
| --- | --- | --- | --- |
| Node restart | 旧recordはstale後60秒でclosedになる | 旧sessionはTTL失効し、新Nodeは新session | 変わらない |
| Reset | 消える | すべて無効 | 変わらない |
| Operator session revoke / 8時間expiry | 変わらない | 変わらない | 対象sessionだけ無効 / enrollmentは不変。expiry後の新URLはTeamserver再起動で発行 |
| Teamserver restart | 消える | すべて無効 | 三roleとenrollmentをすべて更新 |

### 完了条件

state reset、Operator sessionのrevoke / expiry、Teamserver restartによる全token rotationを区別できること。

## Lab 10: Cursor差分同期、共有note、secret-free observability

### 目的

同一lockのcurrent snapshot、cursor-based event / audit delta、Operator共有note、report / metrics、HTTP access logの違いを整理します。

### 手順

1. 二つの Node を登録し、Admin、Operator、Viewer URLを別tabで開きます。Browser developer toolsのNetworkを開き、UIが3秒ごとに通常の`GET /lab/sync`を短pollし、WebSocket、SSE、長時間接続を作らないことを確認します。
2. 初回syncの`events_after`、`audit_after`、`limit`と、responseの`stream_id`、`cursors`、`high_watermarks`、`oldest_available`、`cursor_reset`、`has_more`を確認します。`stream_id`は非秘密のprocess世代識別子、`limit`は最大100です。値そのものにtokenが含まれないことも確認します。
3. AdminとOperatorの各tabから一件ずつ固定taskを登録し、taskの`created_by`がそれぞれ`local-admin`、`task-operator`になることを確認します。Viewerはtaskを登録できません。
4. Admin tabから`admin handoff note`、Operator tabから`operator handoff note`という共有メモを投稿します。どちらも`operator.note`として全tabへ表示され、actorが投稿したprincipalになることを確認します。Viewerの投稿controlは無効で、server側も`note_write`不足を403で拒否します。
5. noteが1〜240文字のplain textで、retained上限が100件であることをsource / testから確認します。note本文にtokenや実在する対象は使いません。
6. Events を actor で分類します。

   - `local-admin` / `task-operator`: 認証済みprincipalによるtask受付、共有note、queued taskの取消、Reset（Resetは`local-admin`だけ）
   - `node`: enrollment、poll による配送・再配送、結果、disconnect event
   - `teamserver`: disconnect時の未処理task cleanup、timeout、queue/session期限、closed Node / terminal taskの整理

7. Auditの`operator.note`にはaction、actor、outcome、固定reasonだけがあり、note本文が複製されていないことを確認します。Node terminal、task payload / resultにもnoteが現れないことを確認します。
8. 二回目以降のsyncで前回cursorがqueryへ使われ、新しいevent / auditだけが返ることを確認します。Reset後はeventの`cursor_reset: true`になり、UIがReset前のevent / note履歴をretained pageへ置き換えることを確認します。retention gapと未来cursorも同じflagで回復します。Teamserver restartでは`stream_id`が変わり、UIが旧cursorを破棄して0から取り直すことをtestで確認します。
9. correlation ID ごとに lifecycle event をまとめます。
10. task data から次の値を計算します。

   - Node 別 completed / failed / timeout / cancelled / expired 件数
   - created から dispatched までの平均待ち時間
   - dispatched から completed までの平均処理時間
   - task type 別の件数

11. 計算結果は event と state の read-only な派生物であり、元の task status を変更しないことを確認します。
12. ViewerまたはOperator sessionで`GET /lab/metrics`を読み、HTTP件数、`GET` / `POST` / `OTHER`、normalized route、status class、duration、worker rejection、`access_log_drops`、lab件数、readinessだけがあることを確認します。metrics取得に`task_write`は不要ですが、tokenなしでは401です。
13. Teamserver terminalの1行JSON access logを観察します。`/lab/tasks/{task_id}/cancel`と`/lab/operators/{session_id}/revoke`が`:task_id` / `:session_id`へ正規化され、`/lab/sync`と`/lab/notes`も固定routeになることを確認します。principalは固定`local-admin` / `task-operator` / `read-viewer`、Node requestでは固定`node`です。
14. access logとmetricsにtoken、Authorization header、sync query、note本文、raw task / session / Node ID、request / response body、exception textがないことを確認します。access logは256件のbounded queueを使い、sink停止や満杯時もrequestをblockせずentryをdropしてcounterだけを増やします。実tokenを検索用にcopyしないでください。
15. sync、note、health、metrics、logのcontractはcore / server / dashboard testでも確認します。

   ```console
   python3 -m unittest tests.test_core tests.test_server tests.test_dashboard tests.test_observability -v
   ```

### 確認問題

- Teamserver に event を集約すると、Browser を閉じている間の Node 活動も観察できるのはなぜですか。
- 三つの独立したfull-history requestより、同一lockの`/lab/sync`が一貫したsnapshotを返しやすいのはなぜですか。
- `cursor_reset`はretention gapとReset後の古いclient cacheをどう回復しますか。なぜ永続監査の完全性は保証しませんか。
- WebSocketやlong pollを使わないことは、16 HTTP workerとNode pollをどう保護しますか。
- note本文をevent feedには表示しつつ、audit、access log、Nodeへ複製しない理由は何ですか。
- taskの`created_by`とeventのactorは、複数Operatorのどの違いを示しますか。
- event 上限 500 は、監査保証と教材の資源制限のどちらを優先した設計ですか。
- task上限500で最古terminalだけを`task.pruned`にし、queued / dispatchedを残す理由は何ですか。
- running exerciseの参照taskと、retention整理後のbounded result ACKを別に保護する理由は何ですか。
- report を生成する処理に task 登録権限が不要なのはなぜですか。
- `/healthz`が200でも`/readyz`が503になり得るのはなぜですか。
- metricsを認証済みread-onlyにし、access logをnormalized routeへ限定することで何を防ぎますか。
- principalをactorへ残しながら、raw session / task / Node IDをaccess logへ残さない設計は、何を区別し何を秘匿しますか。

### 完了条件

state store、atomic snapshot、cursor delta / gap recovery、Operator note、task attribution、report / metrics projection、liveness / readiness、access logの責務とsecret境界を分けて説明できること。

## Lab 11: 参考アーキテクチャと安全な差異

### 目的

実製品の公式資料から抽象概念だけを取り出し、安全境界を保って教材へ置き換える方法を学びます。

### 公式資料

- [Cobalt Strike: Starting the Team Server](https://hstechdocs.helpsystems.com/manuals/cobaltstrike/current/userguide/content/topics/welcome_starting-cs-team-server.htm)
- [Cobalt Strike: Distributed and Team Operations](https://hstechdocs.helpsystems.com/manuals/cobaltstrike/current/userguide/content/topics/welcome_distributed-and-team-ops.htm)
- [Cobalt Strike: Data Model](https://hstechdocs.helpsystems.com/manuals/cobaltstrike/current/userguide/content/topics_aggressor-scripts/as_data-model.htm)
- [Sliver: Getting Started](https://sliver.sh/docs/?name=Getting+Started)
- [Sliver: Multi-player Mode](https://sliver.sh/docs/?name=Multi-player+Mode)
- [Sliver: Custom Clients](https://sliver.sh/docs/?name=Custom+Clients)
- [Sliver: Beacons vs Sessions](https://sliver.sh/tutorials/?name=2+-+Beacons+vs+Sessions)
- [Mythic: Operators](https://docs.mythic-c2.net/operators)
- [Mythic: Operational Pieces](https://docs.mythic-c2.net/operational-pieces)
- [Mythic: Overview](https://docs.mythic-c2.net/home)

### 対応表

| 参考概念 | 本教材 | 意図的に除外したもの |
| --- | --- | --- |
| 中央 Team Server | memory-only localhost Teamserver | 外部 bind、実対象の data、永続 log |
| operator client / multiplayer | 固定roleのlocalhost Browser tab、`created_by` | remote multi-user service、remote listener、永続account、実 command console |
| beacon check-in | foreground Node poll | implant、background service、外部 callback |
| task / result | 固定JSON task、task固有result、purple_labのbounded evidence | shell、workspace外のhost file、任意OS operation |
| sleep / jitter | SLEEP task で poll 間隔とジッターを遠隔変更 | 実 beacon の evasion、無制限の間隔 |
| exit | EXIT task で Node を遠隔正常停止 | 実 implant の kill、永続化解除 |
| task history | correlation ID と central event | tamper-proof 永続監査 |
| shared data / event stream | 同一lockの`/lab/sync`、short poll、cursor gap recovery | WebSocket、SSE、gRPC stream、long poll、外部message broker |
| event feed / comment | 最大100件のmemory-only plain-text `operator.note` | 永続chat、外部notification、Nodeへのmessage転送 |

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
- [ ] Operator writeのpermissionとevent / audit actorがserverで検証される
- [ ] taskの`created_by`が認証済みprincipalから決まり、Browserから指定できない
- [ ] syncは同一lock、最大100件のcursor page、gap recovery、short pollだけを使う
- [ ] noteは`note_write`、1〜240文字、最大100件で、Node / audit / logへ本文を渡さない
- [ ] remote multiplayer、WebSocket、SSE、long pollを追加しない
- [ ] access log / metricsへtoken、query、動的ID、bodyを残さない
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

## Lab 13: SLEEP — poll間隔とジッターの遠隔変更

### 目的

Cobalt Strike の `sleep` コマンドに相当する SLEEP task で、Node の check-in 間隔とジッターを Operator から遠隔変更し、Teamserver 側の状態も追従することを確認します。

### 前提知識

beacon ベースの C2 では、check-in 間隔とジッター（ランダム幅）を変えることで通信パターンを制御します。本教材では poll 間隔 250〜3000 ms、ジッター 0〜50% の範囲で安全に同じ概念を体験します。

### 手順

1. ジッター付きの `training` Node を起動します。

   ```console
   python3 -m c2lab node \
     --name jitter-node \
     --profile training \
     --poll-ms 500 \
     --jitter 20
   ```

2. Node terminal に `jitter=20%` が表示されることを確認します。
3. UI の Node card で `POLL` が `500 ms ±20%` と表示されることを確認します。
4. `RUNTIME_STATUS` を実行し、result に `poll_interval_ms: 500` と `jitter_percent: 20` があることを確認します。
5. Task composerで`SLEEP`を選び、Poll間隔を`2000 ms`、Jitterを`40%`に設定します。スライダーと数値入力が同期すること、折りたたみ式の送信previewが次の固定payloadになることを確認して登録します。

   ```json
   {
     "interval_ms": 2000,
     "jitter_percent": 40
   }
   ```

6. task が completed になった後、Node terminal に `interval=2000ms jitter=40%` が表示されることを確認します。
7. UI の Node card で `POLL` が `2000 ms ±40%` に更新されることを確認します。
8. 再度 `RUNTIME_STATUS` を実行し、`poll_interval_ms: 2000` と `jitter_percent: 40` に変わったことを確認します。
9. Task detailのresultに`previous_interval_ms`、`new_interval_ms`、`jitter_percent`が含まれることを確認します。`task.completed` eventはtypeとcorrelationだけを持ち、result本文は複製しません。
10. Node terminal で poll 間隔が実際に変わったことを、check-in のタイミングから観察します。

### バリデーション確認

数値入力で範囲外の値が送信前に拒否されることを確認します。さらに、次の不正なpayloadをAPIへ送った場合もTeamserverが拒否し、Browser側の制約だけに依存していないことを確認します。

| payload | 拒否理由 |
| --- | --- |
| `{"interval_ms": 100, "jitter_percent": 0}` | interval_ms が 250 未満 |
| `{"interval_ms": 5000, "jitter_percent": 0}` | interval_ms が 3000 超 |
| `{"interval_ms": 1000, "jitter_percent": 60}` | jitter_percent が 50 超 |
| `{"interval_ms": 1000}` | jitter_percent が欠落 |

```console
python3 -m unittest tests.test_node.NodeExecutorTests.test_sleep_updates_executor_poll_state_only_after_acknowledgement -v
python3 -m unittest tests.test_protocol.TaskResultContractTests.test_sleep_result_must_match_requested_interval_and_jitter -v
```

### 確認問題

- SLEEP の result が `new_interval_ms` と `previous_interval_ms` の両方を返す理由は何ですか。
- Nodeが新しいpoll設定をTeamserverのresult acknowledgement後にだけcommitする理由を、timeout後の409とstate整合性から説明してください。
- Teamserver の node record にも新しい `poll_interval_ms` と `jitter_percent` が反映される必要があるのはなぜですか。
- ジッターを 50% に制限する理由と、制限がなかった場合に起こり得る問題を考えてください。
- `basic` profile の Node で SLEEP を実行するとどうなりますか。code と test から確認してください。

### 完了条件

SLEEP task による poll 間隔・ジッター変更が、Node executor、Teamserver node record、UI 表示の三箇所に反映されることを、task lifecycle event と `RUNTIME_STATUS` の両方から説明できること。

## Lab 14: EXIT — Node の遠隔正常停止

### 目的

Cobalt Strike の `exit` コマンドに相当する EXIT task で、Operator から Node を遠隔で正常停止させ、通常の `Ctrl-C` disconnect との違いを確認します。

### 手順

1. `training` Node を起動します。

   ```console
   python3 -m c2lab node --name exit-target --profile training
   ```

2. UI から `PING` を実行し、task が正常に完了することを確認します。
3. `EXIT` を payload `{}` で登録します。
4. 次を順に確認します。

   - task が `completed` になり、result が `{"acknowledged": true}` であること
   - Node terminal に `EXIT received — shutting down.` が表示されること
   - Node process が正常終了すること（exit code 0）
   - UI で Node が `OFFLINE` になり `SESSION CLOSED` が表示されること
   - Events に `task.completed` と `node.disconnected` があること

5. `Ctrl-C` で停止した場合と EXIT で停止した場合を比較します。

### 比較

| 操作 | trigger | disconnect | 未処理 queued task |
| --- | --- | --- | --- |
| `Ctrl-C` | Operator が Node terminal で操作 | Node が `/node/v1/disconnect` を送信 | `failed` (node_disconnected) |
| EXIT task | Operator が UI から登録 | EXIT result 送信後に Node が `/node/v1/disconnect` を送信 | `failed` (node_disconnected) |
| process kill | OS or 障害 | disconnect なし、stale → session TTL 失効 | stale 中は `queued` のまま、TTL 後に `failed` |

### EXIT の後に task を登録する

1. EXIT 完了後、同じ Node ID に task を登録しようとし、`SESSION CLOSED` のため選択不能であることを確認します。
2. 別名で新しい Node を起動し、同じ enrollment token で登録できることを確認します。

```console
python3 -m unittest tests.test_node.NodeExecutorTests.test_exit_returns_acknowledged -v
python3 -m unittest tests.test_protocol.TaskResultContractTests.test_exit_result_must_be_acknowledged -v
python3 -m unittest tests.test_node.NodeExecutorTests.test_basic_profile_rejects_sleep_and_exit -v
```

### 確認問題

- EXIT が result を返してから disconnect する順序はなぜ重要ですか。
- EXIT を `basic` profile で実行するとどうなりますか。
- EXIT と reset の違いを、影響範囲（単一 Node / 全 state）から説明してください。
- 遠隔停止ができることと、遠隔で任意コマンドを実行できることはなぜ同じではありませんか。

### 完了条件

EXIT task による遠隔正常停止が、result 送信 → disconnect → Node process 終了の順序で行われ、`Ctrl-C` と同じ graceful shutdown 経路を使うことを説明できること。

## Lab 15: ATT&CK scenario、検知、封じ込め

### 目的

固定synthetic playbookの結果をATT&CK techniqueへ対応付け、Teamserverがdeterministic alertを生成し、adminがcontrol-plane containmentを適用する流れを観察します。実hostの監視や隔離は行いません。

### 手順

1. `purple_lab` Nodeを起動し、Admin URLとOperator URLを別tabで開きます。
2. Operator tabの「ATT&CK演習」で`DISCOVERY_COLLECTION`を開始します。viewerでは開始できず、operatorは`exercise_write`で開始できることを確認します。
3. 次の順序をTasks、ATT&CK演習、Eventsで追跡します。

   ```text
   exercise.started
      ├─ DISCOVERY_FIXTURES completed ─> DET0370 / T1083 alert
      └─ COLLECT_AND_STAGE completed ──> DET0380 / T1005 alert
                                        DET0261 / T1074.001 alert
   ```

4. 最初のalertが出た時点でAdmin tabから`CANCEL_REMAINING`を適用し、残るscenario taskだけが`cancelled`になることを確認します。operator tabでは`containment_write`不足により操作できません。
5. Reset後に同じscenarioを開始し、最初のalert後に`PAUSE_NODE_TASKING`を適用します。
6. Nodeはpollを継続して`online`のままですが、UIに`TASKING PAUSED`が表示され、新規taskとexercise開始が無効になること、APIでも`409 node_tasking_paused`になることを確認します。
7. ResetでNode / task / exercise / alert / pauseが消え、foreground Nodeが再登録することを確認します。
8. `CANARY_REMOVAL`も実行し、`CREATE_CANARY`だけではalertが出ず、固定artifactの`CLEANUP`完了後だけDET0140 / T1070.004が記録されることを確認します。

### 境界確認

- scenario開始bodyは`node_id`と`scenario_id`だけで、余分な`steps`、`path`、`content`は400になる
- alertは検証済み`completed` taskからのみ生成され、Node result内の任意ruleを信用しない
- `PAUSE_NODE_TASKING`はTeamserverのqueue / dispatchだけを止め、OS process、firewall、account、network interfaceを変更しない
- ATT&CK mappingはeducational metadataで、実環境の検知coverageを意味しない
- exerciseは最大50件、timelineは各16件で、Reset / restart後に復元しない

```console
python3 -m unittest tests.test_exercises -v
python3 -m unittest tests.test_server.OperatorRBACServerTests.test_exercise_api_enforces_catalog_schema_rbac_and_containment -v
```

### 完了条件

fixed scenario → validated result → detection alert → admin containment → Resetという状態遷移と、実host操作を行わない境界を説明できること。

## Lab 16: Graph → Operation → Detection → Containment

### 目的

`Graph View`でsynthetic relationshipを読み、`Operation Builder`でordered fixed playbookをatomicにqueueし、fixed exerciseで同じ経路のobserved detectionとcontainmentを確認します。Graph Viewは既存`/lab/sync` snapshotのread-only projectionであり、実host、domain、user、credentialを探索しません。

### 手順

1. `purple_lab` Nodeを起動し、Admin URLとOperator URLを開きます。Graph Viewの`C2トポロジー`で、Node → task → playbookのhandoffだけが表示され、実hostやdomain topologyが作られないことを確認します。
2. `Synthetic Attack Path`で`DISCOVERY_COLLECTION`を選び、catalog由来のT1083、T1005、T1074.001が`PLANNED`として表示されることを確認します。`PLANNED`は実行・検知済みという意味ではありません。
3. Operator tabの`Operation Builder`で同じNodeを選び、step 1を`DISCOVERY_FIXTURES`、step 2を`COLLECT_AND_STAGE`にして送信します。1〜3件だけを指定でき、順序を入れ替えられることも確認します。
4. 二つのtaskが一つのoperation ID、`operation_step` 1 / 2でまとめてqueueされ、C2トポロジーから各taskとplaybook detailへ移れることを確認します。片方だけが先に登録される状態は作られません。
5. Nodeが完了したら、C2トポロジーでoperation taskが`completed`になり、task detailでvalidated technique / evidenceを確認できることを観察します。operation単体ではSynthetic Attack Pathのexercise alertもcontainment actionも生成されません。
6. 続けて固定exerciseの`DISCOVERY_COLLECTION`を開始します。Synthetic Attack Pathでplanned relationを確認し、exercise所有taskが完了するとDET0370 / T1083、DET0380 / T1005、DET0261 / T1074.001が`OBSERVED` alertとして追加されることを確認します。
7. 最初のalert後、Admin tabから`CANCEL_REMAINING`を適用します。Graph Viewでcontainmentと残りtaskの`cancelled`を追い、Operator tabでは`containment_write`不足で適用できないことを確認します。

### 境界確認

- Graph Viewは`/lab/sync`の`nodes`、`tasks`、`scenario_catalog`、`exercises`だけを関連付け、別のenumeration requestを送らない
- operation bodyは`node_id`、1〜3件のexact `{"playbook":"..."}` step、任意queue TTLだけで、unknown ID、4件目、余分な`command`、`path`、`host`、`content`を拒否する
- Teamserverは全step、`purple_lab` profile、Node状態、queue capacityを一つのatomic operationとして検証し、失敗時はtaskを一件も作らない
- operation groupingはOperator read modelだけで、Node protocolは固定`RUN_PLAYBOOK`のまま。任意command、path、network executionへ拡張されない
- `OBSERVED`はsynthetic fixtureのvalidated result / alertを意味し、実環境の侵入経路、権限関係、credential、検知coverageを意味しない
- terminal taskがretention上限で整理されても、allowlist済みTechnique IDだけをexercise timelineから復元し、payloadやresult全体は保持しない

### 完了条件

planned graph → atomic fixed-playbook operation → validated task evidence → fixed exerciseのobserved detection → admin containmentを、Node / task / playbookのhandoffと安全境界を含めて説明できること。

## まとめ課題

次の問いへ、Current state、Events、terminal output、source/test のうち二種類以上を根拠として答えてください。

1. Operator が task を登録してから Node result が表示されるまで、どの process と状態を通りますか。
2. Operator token、enrollment token、per-node session tokenは、用途、保存形式、失効条件がどう異なりますか。
3. stale後60秒以内、stale session TTL失効後、正常disconnect後で、新規taskと未処理taskの扱いはどう異なりますか。
4. correlation ID は task ID だけでは解きにくい何を説明しますか。
5. `RUNTIME_STATUS` と `HASH_TEXT` が実 host の列挙や file hash にならない根拠は何ですか。
6. Cobalt Strike、Sliver、Mythicから取り入れたcontrol-plane概念と、意図的に除外したlistener、payload、shell、remote multiplayer、streaming APIをそれぞれ挙げてください。
7. Node が別 process でも、本教材が別の物理端末を制御できない理由を code と CLI から説明してください。
8. poll response と result response が失われた場合、再配送、outbox、冪等受付がそれぞれ何を回復し、何を保証しないか説明してください。
9. `purple_lab`の実I/Oがworkspace外へ到達せず、session失効時にartifactを引き継がない根拠を説明してください。
10. queue TTL、cancel、`Idempotency-Key`、terminal-only retentionがNode capabilityを増やさない理由を説明してください。
11. `admin` / `operator` / `viewer`の固定permissionと401 / 403の違いを、`/lab/session`とadmin-only session revokeへ対応付けてください。
12. `/healthz`、`/readyz`、`/lab/metrics`、normalized JSON access logが、それぞれ何を公開し何を残さないか説明してください。
13. `SIGTERM`がgraceful cleanupを試みても、`SIGKILL`後のworkspace cleanupを保証できない理由を説明してください。
14. `/lab/sync`が同一lock snapshot、cursor page、`cursor_reset`を使って複数tabのhistoryをどう収束させるか説明してください。
15. taskの`created_by`と共有noteのactorは何を区別し、note本文をNode / audit / access logへ複製しないのはなぜか説明してください。
16. admin / operatorの`note_write`とviewerのread-only表示を、UI補助とserver-side認可の両面から説明してください。

17. SLEEPがNode executor、Teamserver node record、UIのPOLL表示を同時に更新する必要があるのはなぜですか。`RUNTIME_STATUS`のidentity検証との関係も説明してください。
18. EXIT taskによる遠隔停止と`Ctrl-C`による手動停止が同じgraceful shutdown経路を使う理由を、disconnect順序とqueued task cleanupから説明してください。
19. SLEEPとEXITが`basic` profileで拒否される理由を、Node executorとTeamserverの二段階検証から説明してください。
20. ジッターの上限が50%で、poll間隔が250〜3000 msに制限されている安全上の意味を説明してください。
21. `exercise_write`と`containment_write`を分け、adminだけが`PAUSE_NODE_TASKING`を使える理由を説明してください。
22. DET0370 / DET0380 / DET0261 / DET0140のmetadataと、教材固有signal / thresholdを区別してください。
23. Nodeが`TASKING PAUSED`でもpollを続けることと、OS / network isolationではないことを対応付けてください。
24. Graph Viewの`PLANNED`と`OBSERVED`は何を根拠にし、なぜ実host / domain / user / credentialのrelationshipを表さないのですか。
25. Operation Builderが1〜3件をatomicにqueueしながら、Node capabilityや`RUN_PLAYBOOK` payloadを拡張しない仕組みを説明してください。
26. operation単体のobserved techniqueと、fixed exerciseが生成するdetection / containmentを分ける理由を説明してください。

すべて説明できれば、中央 Teamserver、固定roleのOperator、foreground Node、poll、task/result、correlation、atomic cursor sync、Graph View、bounded Operation Builder、共有note、bounded observability、SLEEP/EXIT、ATT&CK検知・control-plane containment、安全境界という本教材の主要概念を一通り確認できています。
