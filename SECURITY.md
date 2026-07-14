# Security Policy

## 対象と安全目標

C2 Lab Framework は、C2の制御面と、Node-private temporary workspaceに限定された小さなdata-plane演習を学ぶlocalhost教材です。`purple_lab`は実際のfile I/Oを行いますが、対象はNode自身が生成したsynthetic artifactだけです。別の物理端末を管理する製品ではありません。

安全目標は次のとおりです。

- Node と Teamserver の通信を同じ PC の loopback に閉じ込める
- 任意shell、process起動、host file操作、OS列挙への経路を持たない
- task を固定列挙と厳密な JSON schema に限定する
- enrollment、Node session、Operator の秘密を役割ごとに分離する
- 不正入力、停止 Node、過負荷を安全側に処理する
- application state と session を永続化しない
- UIとlogで常に教材であり、実I/OもNode-private workspaceとsynthetic fixtureだけを扱うことを示す

安全性は Node を強力な sandbox へ隔離することではなく、Node のコードに危険な能力を実装しないことによって成立します。

## 明示的に存在しない能力

- shell、PowerShell、任意 command、任意 script、任意 code evaluation
- child process の起動、service 登録、login 時起動
- user 指定 file の読込、書込、列挙、upload、download
- 実 host の user、process、network、software、credential の収集
- loopback 以外への bind、callback、listener、controller 接続
- Node binary、payload、installer の生成または配布
- database、task history、Node session の永続化
- 自己複製、自動再起動、watchdog
- traffic 偽装、難読化、秘匿、検知回避
- runtime plugin、hook、動的 module loading

Teamserver は同梱 Operator UI を配信するため、固定許可リストにある静的 asset だけを読みます。request path を任意 file path として解決しません。`purple_lab`以外のNode taskからfile systemへ到達する経路はなく、`purple_lab`もNode自身が生成した一時workspace外へ到達するinterfaceを持ちません。

## 信頼境界

```text
Browser tab
  operator token
       │
       ▼
127.0.0.1 Teamserver ── enrollment token ──> initial Node enrollment
       ▲                                        │
       │                per-node session token  │
       └────────────── poll / result / disconnect
```

### Browser Operator

Browser は表示と操作を担当しますが、信頼できる入力元とは見なしません。task type、payload、Node ID、queue TTL、cancel対象、`Idempotency-Key`はTeamserverで再検証されます。UI が送る write request は、operator token に加えて同じ localhost `Origin` を要求します。

UI は user data を HTML として挿入せず、text node として表示します。Content Security Policy、`frame-ancestors 'none'`、`X-Frame-Options: DENY`、`nosniff`、`no-referrer`、`no-store` を送信します。

### Teamserver

Teamserver は Node、task、event の authoritative state を持ち、task の受付、配送、期限、結果の対応を判断します。application state はすべて memory 内です。

Teamserver は `127.0.0.1` へ固定 bind されます。request の接続元 IP と `Host` header の両方が loopback / localhost でなければ拒否します。bind address を変更する CLI option はありません。

### Foreground Node

Node は別 process ですが、常に foreground で動作します。controller URL validator は次を強制します。

- scheme は `http`
- host は `127.0.0.1` または `localhost`
- userinfo、query、fragment、追加 path は禁止
- port だけを変更可能

`localhost` は canonical な `127.0.0.1` URL へ正規化されます。Node client は system proxy 設定を無効化し、HTTP redirect を拒否し、response を 32 KiB に制限します。

Node は loopback HTTP の poll、result、disconnect 以外の外向き request を行いません。Node handler は固定 task registry の値だけを処理します。

Node は完全な隔離環境ではありません。同じ source tree を改変できる利用者は境界を変更できるため、教材は信頼できる local source から実行してください。

### Purple Lab workspace invariant

`TemporaryDirectory`はOS sandboxや権限分離ではありません。境界は、workspace rootをNode自身だけが決め、caller-supplied pathを持たず、固定名・固定処理だけを実装することで成立します。

1. workspace rootは`purple_lab` Nodeの起動時に生成し、CLI、API、task payloadから受け取りません。
2. `RUN_PLAYBOOK` payloadはexact `{"playbook": <固定ID>}`だけです。
3. logical artifact registry、fixture内容、step、ATT&CK mappingはcode内の固定値です。実file名は固定prefixとrandom suffixでexclusive作成し、Operatorへ公開しません。
4. evidenceはlogical name、size、digest、count、statusだけを返し、absolute pathとraw contentを返しません。
5. Nodeごとに別workspaceを使い、正常終了とsession失効時に破棄・再生成します。
6. shell、subprocess、network client、archive、recursive delete、runtime pluginを使用しません。
7. `SIGKILL`やOS crashではcleanupが走らない可能性があるため、非機密のsynthetic dataしか書きません。

同じOS userによるsource改変やworkspaceへの直接干渉は脅威モデル外です。詳細は[Purple Lab実挙動ガイド](docs/PURPLE_LAB.md)を参照してください。

## 認証と token

### Operator token

Teamserver 起動時に生成され、`Operator URL` の `#token=...` fragment として表示されます。URL fragment は通常の HTTP request や Referer へ含まれません。UI は token を読み取ると fragment を address bar から除去し、現在の tab の `sessionStorage` へ保持します。

Operator API は `Authorization: Bearer <token>` を要求します。静的 UI と `/healthz` は token なしで取得できますが、lab state は返しません。

### Enrollment token

Operator token とは別に起動時生成され、Node の最初の `/node/v1/enroll` だけに使用します。同じ Teamserver 実行中に複数 Node を登録できる token であり、一回限りではありません。

`--enroll-token TOKEN` は再現性のため利用できますが、shell history や process listing に露出する可能性があります。通常は option を省略し、非表示 prompt へ入力してください。

### Per-node session token

登録が成功すると、Teamserver は Node ごとに十分長い session token と Node ID を発行します。以降の Node protocol は次の組合せを要求します。

- `Authorization: Node <session-token>`
- `X-C2Lab-Node: <node-id>`

session token は Node process の memory にのみ保持され、通常は画面へ表示しません。ある Node の token を別 Node ID に組み合わせても認証されません。

### 失効

- `/lab/reset` は Node state を削除し、既存の per-node session token をすべて無効化します。operator token と enrollment token は同じ Teamserver process 内では変わりません。
- Teamserver restart は state と全 token を破棄し、新しい operator / enrollment token を生成します。
- Node の正常終了は `/node/v1/disconnect` を送り、status を `offline`、`session_active` を `false` にして session token を失効させます。旧 token による poll は認証されません。
- stale 判定による `offline` は別の状態です。60秒の回復猶予中は`session_active: true`のままで、同じprocessがpollを再開すると`online`へ戻れます。猶予を過ぎるとTeamserverはsession tokenを失効させ、未処理taskを`failed`にして`node.session_expired`を記録します。

token、Operator URL、認証 header を screenshot、log、chat、課題提出物へ含めないでください。漏えいが疑われる場合は Teamserver を終了し、再起動してすべての token を更新します。

## Protocol と入力検証

task type は次の七種類だけです。

- `PING`
- `RUNTIME_STATUS`
- `ECHO_TEXT`
- `HASH_TEXT`
- `WAIT`
- `GENERATE_EVENT`
- `RUN_PLAYBOOK`

自由形式の command field はありません。各 payload は field の完全一致、型、文字数、値域を検証し、余分な field も拒否します。

profile は固定 task の部分集合です。登録時に Node が送る capability list は、Teamserver が保持する `basic`、`training`、`purple_lab` profileのいずれかと完全一致する必要があります。`RUN_PLAYBOOK`は`purple_lab`だけに含まれます。Node が未定義 capability を自己申告して増やすことはできません。

Node result は `completed` または `failed` と JSON object だけを許可し、4096 bytes に制限します。さらにTeamserverがtask type、queued payload、enrolled Node identityに対応するexact result schemaと固定値を検証します。失敗結果も任意文字列ではなく固定`error_code`だけです。task の `node_id` が認証済み Node ID と一致し、状態が `dispatched` の場合だけ初回結果を受理します。すでに確定した task への同一 status・同一 result の再送は冪等に成功し、counter や completed / failed event を重複させません。異なる結果は `409 result_conflict` です。

task登録の任意`queue_ttl_seconds`は整数5〜86400だけを許可し、省略時は300です。任意`Idempotency-Key`は8〜128文字の英数字と`-_.:`だけを許可します。同じkey、Node、type、payload、queue TTLの再送はretained memory state内で同じtaskを返し、異なるrequestへのkey再利用は`409 idempotency_conflict`です。keyは内部deduplication metadataであり、Node、公開task、event、audit、reportへ渡しません。

Operator cancel APIは認証とlocalhost Originを要求し、`queued`だけを`cancelled`へ遷移させます。同じcancelled taskへの再送は冪等で、dispatchedまたは他のterminal stateは`409 task_not_cancellable`です。取消は新しいtask typeやNode capabilityを追加しません。

task contractに一致しない初回resultは`invalid_result`として拒否し、taskを`dispatched`のまま保ちます。`task.result_rejected` event/auditには固定reasonだけを記録し、不正result、absolute path、raw content、exception textをコピーしません。

Node client も Teamserver response を無条件には信用しません。enrollment response の Node ID と session token、poll response の task ID、correlation ID、Node ID、status、type、payload が期待する形式と固定 schema に一致することを確認してから保持・実行します。raw timeout や response 読込み中の切断も client error へ正規化し、foreground loop の再試行対象にします。

## 非同期状態と障害処理

- 一つの Node へ同時に配送する task は一件だけです。
- queued task は作成順に配送します。
- queued taskは既定300秒、指定時5〜86400秒のqueue TTLを持ち、期限まで未配送なら`expired`になります。dispatch時にqueue期限は解除されます。
- Operatorはqueued taskだけを`cancelled`にできます。`cancelled`と`expired`はいずれもNodeへ配送されません。
- 配送時に 8 秒の deadline を設定します。
- result が未確定の間、同じ session の poll には同じ dispatched task を再配送します。`delivery_attempts` を増やして `task.redelivered` を記録しますが、deadline は延長しません。
- Node は実行後の pending result を memory outbox に保持し、acknowledgement まで新規 poll より先に同一内容を再送します。process 終了時には outbox も消えます。
- Teamserver ResetやrestartでNode sessionが401になった場合、Nodeはpending resultを破棄し、`purple_lab` workspaceも破棄してから新しいsessionへ再登録します。
- deadline までに result がなければ `timeout` にし、遅れて届いた result は `409 invalid_task_state` で拒否します。
- stale offline後60秒はsessionが有効なため、queue TTL内の未配送taskは`queued`のまま待ちます。pollが再開すると同じNode IDでonlineに戻り、配送できます。60秒を過ぎるとsessionを失効させ、残る`queued` / `dispatched` taskを`failed`にします。
- 正常 disconnect は session を失効させ、対象 Node の `queued` と `dispatched` task を直ちに `failed` にします。event data の reason は `node_disconnected` です。
- `session_active: false` の Node への新規 task は `409 node_disconnected` で拒否します。
- Node の最終 poll から `max(8秒, poll間隔×3)` を過ぎると offline にします。
- task ID に加えて correlation ID を発行し、受付、配送、再配送、完了、失敗、timeout、取消、queue期限切れ、retention整理のeventを対応付けます。

正常終了できない Node、network error、HTTP response の消失は想定内です。poll response が失われ、Teamserver だけが task を dispatched にした場合は、期限内の次回 poll で同じ task を再配送します。result request が到達しなければ outbox から再送し、Teamserver が result を確定した後に response だけが失われても、同一結果の冪等受付で収束します。Node は Teamserver が一時的に利用できない場合も foreground のまま待ち、session 認証が失効した場合は保持している enrollment token で再登録を試みます。これは memory-only の限定的な回復であり、process crash や deadline 超過を越える exactly-once 保証ではありません。

## Audit、report、UI filter

`/lab/audit` と `/lab/report` はoperator認証を必要とするread-only projectionです。どちらも既存のbounded memory stateだけを読み、task登録、Node state変更、外部送信、disk保存を行いません。responseにはoperator token、enrollment token、per-node session token、内部deadlineを含めません。

eventとauditの `sequence` はTeamserver process内の順序を比較する補助値です。ResetはNode、task、retained eventを消去し、process内counterを進めたまま `lab.reset` をeventとauditへ記録します。Teamserver restartではauditとcounterを含む全memory stateが失われます。どちらのsequenceも欠落検知や改ざん耐性を保証しません。reportの集計値も、その時点のmemory snapshotから計算した教材用projectionであり、監査証跡やcompliance reportではありません。

KPI、navigation、task lifecycle filter、audit filterは表示を絞るだけです。UIで非表示になったrecordもserver-side認可を通過したresponseの一部であり、filterを認可境界として扱いません。filter変更はstateを変更せず、task登録、queued taskの取消、Resetがそれぞれ独立したwrite操作です。

## 資源制限

| 対象 | 上限 |
| --- | ---: |
| HTTP body | 16 KiB |
| HTTP worker | 16 |
| HTTP connection | response ごとに close |
| socket timeout | 5 秒 |
| Node client request timeout | 3 秒 |
| Node HTTP response | 32 KiB |
| Node record | 20。stale offline後60秒でsession失効 |
| 全 task | 500。最古terminalだけをretention整理 |
| 1 Node の queued task | 50 |
| 1 Node の queued `RUN_PLAYBOOK` | 3 |
| queued task TTL | 既定300秒、指定時5〜86400秒 |
| `Idempotency-Key` | 8〜128文字、英数字と`-_.:` |
| 保持 event | 500 |
| 保持 audit entry | 500 |
| result | 4096 bytes |
| text / message | 240 文字 |
| poll interval | 250〜3000 ms |
| WAIT | 0〜2000 ms |
| dispatched task deadline | 8 秒 |

Node record が 20 件に達した状態で登録すると、`session_active: false` のうち最古の record を自動削除して `node.pruned` を記録します。全 record が active なら `429 node_limit` で拒否します。全taskが500件に達すると、`completed`、`failed`、`timeout`、`cancelled`、`expired`のうち最古の一件だけを削除して`task.pruned`を記録します。全件が`queued` / `dispatched`なら削除せず`429 task_limit`です。event log は保持上限へ達すると古い entry から memory 内で置き換えられます。いずれも監査製品の tamper-proof log ではありません。

## 永続化しないもの

- Teamserver の Node、task、event state
- operator / enrollment / per-node session token
- Node の identity と task count
- task result と correlation history
- Node の pending-result outbox
- application log、report、database

`purple_lab` artifactはNode process/session lifetime中だけ一時filesystemに存在します。これはTeamserver stateの永続化ではありません。異常終了時にはOSのtemporary-directory cleanupまで残る可能性がありますが、token、host data、user dataは書き込みません。

Browser の `sessionStorage` は現在の tab での再読込に備えた一時 token 保持です。Teamserver state の永続化ではなく、tab を閉じると browser session の対象外になります。共有 PC では利用後に tab と Teamserver の両方を終了してください。

## 脅威と対策

| 脅威 | 対策 | 残る制約 |
| --- | --- | --- |
| LAN / Internet からの接続 | `127.0.0.1` 固定 bind、peer と Host の検証 | port forward や proxy を利用者が追加すると境界を壊す |
| Node の外部 controller 接続 | URL 検証、localhost の正規化、proxy 無効、redirect 拒否 | source を改変できる local user は対象外 |
| 別 site からの write | operator token、local Origin、CSP | 悪意ある browser extension は対象外 |
| token の役割混同 | Bearer / Enroll / Node の auth scheme 分離、Node ID 併用 | 同じ OS user による memory / terminal 窃取は対象外 |
| 任意 task injection | 固定列挙、profile、strict payload schema | code に危険 task を追加しない review が必要 |
| HTML injection | DOM text rendering、CSP | browser 自体の侵害は対象外 |
| result の偽造・応答消失 | per-node token、ownership/state、task固有result schema、memory outbox、同一結果の冪等受付 | session tokenを得たprocessはowned taskの固定結果を代行でき、process終了後の再送はできない |
| task登録responseの消失・再送 | 任意`Idempotency-Key`で同一requestを一つのretained taskへ収束 | keyを省略した再送、Reset/restart/prune後の再送はdeduplicateしない |
| path traversal / workspace escape | caller-supplied path/filenameなし、固定logical registry、exclusive temp file、regular-fileとcontainment検証 | sourceを変更できる同一userは対象外 |
| playbook injection | 固定ID、exact schema、`purple_lab` profile、3件queue上限 | registryへ危険処理を追加しないreviewが必要 |
| crash後の一時artifact | synthetic dataのみ、正常終了・401時cleanup | 強制終了時はOS temp cleanupまで残る場合がある |
| ATT&CK mappingの過大解釈 | educational metadataと明記 | detection coverageや実targetでのtechnique実行を保証しない |
| resource exhaustion | 件数、size、worker、timeout、queue TTL、terminal-only retention上限 | 敵対的な同一 user 向け multi-tenant service ではない |
| stale / disconnected Node の混同 | `status` と `session_active` を分離し、stale後60秒の回復猶予と自動失効を記録 | process crash の原因分析や同一identityでの自動復旧は行わない |
| 保存データ漏えい | memory-only state、Reset、restart | OS swap、crash dump、screen recording は対象外 |

## 非目標

本教材は次を提供しません。

- 別の物理端末の管理または remote administration
- Internet 公開 service の認証、TLS、account、RBAC、監査保証
- 同じ OS user、administrator、malicious browser extension からの隔離
- multi-tenant isolation、high availability、backup、restore
- hostile traffic に対する完全な DoS 耐性
- 実targetに対するATT&CK technique実行、penetration testing、payload実行、post-exploitation機能。`purple_lab`はsynthetic fixtureへの限定実I/Oだけを扱う

loopback 上では HTTP を使用します。TLS を省略してよい根拠は通信範囲を同じ host の loopback に固定しているためであり、外部公開してよいという意味ではありません。

## 参考元との意図的な差異

Cobalt Strike の公式資料にある中央 Team Server と operator client の分離、および Sliver 公式資料にある非同期 beacon の poll/task/result は、状態管理を学ぶための参考概念です。

- [Cobalt Strike: Starting the Team Server](https://hstechdocs.helpsystems.com/manuals/cobaltstrike/current/userguide/content/topics/welcome_starting-cs-team-server.htm)
- [Cobalt Strike: Distributed and Team Operations](https://hstechdocs.helpsystems.com/manuals/cobaltstrike/current/userguide/content/topics/welcome_distributed-and-team-ops.htm)
- [Sliver: Getting Started](https://sliver.sh/docs/?name=Getting+Started)
- [Sliver: Beacons vs Sessions](https://sliver.sh/tutorials/?name=2+-+Beacons+vs+Sessions)

実製品の listener、implant / payload、外部 transport、operator scripting、command 実行、回避、永続化は転用していません。この差異を縮める変更は安全性向上ではなく、本教材の scope 逸脱です。

ローカルのRamune-C2 commit `f194494` は、control-plane設計を比較するため読み取り専用で調査しました。採用したのはprocess separation、typed task ledger、structured audit / event、KPI / navigation / filterという抽象概念だけです。参照元のsource code、brand、visual asset、UI copy、protocolは使用していません。詳細は[設計参照と安全な採用境界](docs/DESIGN_REFERENCES.md)に記録しています。

参照元に存在する次の領域は、本教材では明示的に不採用です。

- 外部listener、複数transport、relay、peer-to-peer通信
- payload、stager、binary build、配布
- 自由形式task、operator command console、scheduler
- workspace外の実host data、file、process、network、credentialの取得
- background実行、永続化、traffic shaping、秘匿、回避
- pivot、proxy、external webhook、multi-tenant operator account
- runtime plugin、database、永続audit log

これらを認証やallowlistの後ろへ追加する案も、安全境界を満たしません。

## 安全性を壊す変更

次の変更は demo や test 目的でも受け入れられません。

- `0.0.0.0`、LAN address、外部 hostname への bind / connect
- configurable listener、redirector、tunnel、proxy、peer-to-peer transport
- command、script、expression、template の動的実行
- process、shell、system API を使う task
- user path を受け取る file API または file transfer
- payloadへの`path`、`content`、`steps`、`command`、`url`、`host`、`args`、`env`
- workspace root、artifact名、fixture内容のCLI/API設定
- absolute path、raw artifact content、exception textのresult/event出力
- 実 host metadata、environment variable、credential の収集
- background daemon、service、cron、startup entry
- session、task、event の database / file 永続化
- plugin、extension package、runtime code loading
- obfuscation、traffic shaping、evasion、security product bypass

## 変更時の確認

1. `python3 -m unittest discover -s tests -v` が成功する。
2. Teamserver の bind address が `127.0.0.1` に固定されている。
3. Node が loopback 以外の controller URL を起動前に拒否する。
4. operator、enrollment、Node session の auth scheme が混同されない。
5. Reset 後に旧 Node session が使えない。
6. 正常 disconnect 後に旧 session と新規 task が拒否され、未処理 task が failed になる。
7. poll response 消失時に同じ task が期限内再配送され、`delivery_attempts` と `task.redelivered` が増える。
8. result の同一再送は一回の完了として扱い、異なる再送を拒否する。
9. Node 上限では最古の closed record だけを整理し、全 active なら拒否する。
10. HTTP response が connection を閉じ、Node が 32 KiB を超える response を拒否する。
11. stale offline の有効 session が poll で online へ復帰できる。
12. 未定義 task、profile 外 task、余分な field、範囲外入力が拒否される。
13. result の Node ownership、task state、size が検証される。
14. Node client が proxy と redirect を使わず、response size を制限する。
15. input / result が HTML や code として解釈されない。
16. process起動、dynamic evaluation、workspace外の任意file、非loopback通信の経路がない。
17. restart 後に state と token が残らない。
18. `RUN_PLAYBOOK`が`purple_lab`だけに属し、unknown ID、余分なpath/step、workspace escapeを拒否する。
19. resultがtask固有schemaとqueued payloadに一致し、raw dataやabsolute pathを受理しない。
20. session失効とNode終了でpurple workspaceが廃棄される。
21. stale offlineは60秒以内なら回復し、期限後はsessionと未処理taskを閉じる。
22. queue TTLの既定値と5〜86400秒の境界、`expired`への遷移をtestする。
23. cancelはqueuedだけに許可し、cancelledへの再送が冪等で、dispatchedを変更しない。
24. `Idempotency-Key`の文字種・長さ・同一request再送・異内容409をtestする。
25. task上限では最古terminalだけを整理し、queued / dispatchedだけなら拒否する。

CIはpush、pull request、手動実行でPython 3.11〜3.14の`compileall`と全testを実行し、Node.js 22でdashboard JavaScriptの構文を確認します。

## 問題の報告

非loopbackから到達できる、tokenの役割を越えて操作できる、未定義taskを処理できる、Nodeからshell・host file・OS情報へ到達できる、purple workspaceがsessionを越えて残る、といった問題は安全境界の不具合です。

公開 issue へ token、Operator URL、実データを貼らず、repository 管理者が案内する非公開の連絡手段を使用してください。報告には version、最小の再現手順、期待した境界、観測した挙動を含めてください。
