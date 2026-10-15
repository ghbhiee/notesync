# NoteSync 开发记录（中文）

设计文档见 [DESIGN.md](DESIGN.md)。


- 设计文档：[DESIGN.md](DESIGN.md)（先读这个）
- 状态：**M4 完成（2026-08-30）**——launchd 驻留探索（后被 App 宿主取代）+ Google Drive 镜像；48 项测试全绿
- **作用域（用户拍板）：白名单模式** —— 只同步 Apple Notes「SYNC」文件夹 ⇄ `iCloud Drive/SYNC` 根 ⇄ Evernote「SYNC」笔记本，其余笔记一概不碰
- 历史：同日完成 M1 引擎 / M2 Evernote / M3 Apple Notes；全量 bootstrap 实测后按用户要求撤销（Apple/EN 今天创建的内容已全删进各自回收站），改为白名单
- 用法：`python3 -m notesync init|sync|status|watch`；测试：`python3 -m unittest discover -s tests`；守护日志 `~/Documents/NoteSync/notesync.log`
- **M5 完成（2026-08-30）**：NoteSyncBar 菜单栏 app（macapp/，AppKit 单文件 + build.sh 组装 .app + 固定自签证书签名（build.sh 可配 CODESIGN_ID），装于 /Applications，SMAppService 开机自启）。它是 TCC 的 responsible app：launchd 裸 python 被静默拒的 Documents/iCloud 权限，由 GUI app 正常弹窗获得，python watch 作为其子进程继承授权。菜单：状态/立即同步/打开文件夹/看日志/退出。python 子进程必须加 `-u`（否则日志块缓冲看似卡死）

## v2 富格式渲染（2026-09-01）

文件/git 永远存标准 md；写入端点时渲染成各自富格式，读回拍平回 md。核心不变量：**flatten(render(md)) == md**（tests/test_render.py 矩阵 + 双端真实往返验证）。子集外元素保持字面 = 永不丢内容。

| md | Evernote | Apple Notes |
|---|---|---|
| `#` `##` | h1/h2 | 渲染（Apple 存为 b+24/18px span，拍平可逆） |
| `###` | h3 | 渲染（18px+斜体编码——`<h3>` 会退化为 b、<18px 字号被剥，此编码可逆） |
| `**粗**` `*斜*` | 渲染 | 渲染 |
| `- 列表` | ul | ul |
| `1.` `[链接]()` `- [ ]` | 字面 | 字面（Apple 丢 ol 类型/丢 href） |

要点：Apple 的 `<b><span font-size>` 顺序是 b 在外（拍平须撤销已发标记）；「最近删除」里的笔记 byId 仍可读，Apple 的存在性确认必须用文件夹成员资格；`notesync rerender` 一次性把存量字面笔记升级为富格式。

## v3 格式层 + 保真护栏（2026-09-21）

起因：Apple 压扁连续空格、吞行首缩进，代码块和 ASCII 图一旦在 Apple 里被编辑，压扁版就会成为共识写回文件（潜伏的数据丢失）。旧代码真实往返基线：Apple 12/17 篇有损、Evernote 0；v3 上线后两端 0 有损（剩下的差异都是写法规范化，`lint --fix` 可消除）。

- **能力注册表** `notesync/capabilities.py` 是唯一事实源：渲染器按它决定富格式 / 字面，`notesync profile` 生成 `MARKDOWN_PROFILE.md`（测试保证不漂移），lint 的建议、测试矩阵的用例都从它来
- **渲染器重写为块级解析**：围栏代码块（围栏行原样保留在块内，携带语言标签、界定边界；块内不做任何 md 解析）、`$$` 公式块与行内 `$…$`（字面、不解析）、引用（EN 递归渲染）、有序/嵌套列表、宽松表格（冒号/间距/缺竖线，对齐不保留）、行内代码；空白一律写成 `&nbsp;`
- **拍平器重写**（`notesync/flatten.py`，两种方言共用）：按行收集“带格式的文本段”，最后统一序列化——碎片化的粗/斜体自动合并、强调不嵌套、段边空格挪到标记外；整段 `<tt>` = 代码行，一串代码行没有围栏就补上，没闭合就补闭合
- **引擎**：`ep_map.seen_rev` 记端点修改时间戳，`ep_map.shadow` 记端点读回的有损副本。端点“内容变了但时间戳没变” = 读法变化（拍平器升级 / Apple 定型），只重建基线不回写；端点真编辑时按行 rebase 到规范文本上（`merge.rebase`）：用户没碰的行保持规范形（哪怕紧挨着改动行），只是被压了空白的行不算编辑。无时间戳的旧行第一次被看到时直接采纳为基线
- 新命令：`audit [--live]`（真实往返审计，Apple 批量写入后等定型再读）、`lint [--fix]`、`profile`；`rerender` 持锁、跳过有未同步改动的笔记、按读回重建基线
- N(x) 新增：正文开头的空行丢弃（Apple 的标题行会吞掉它，本来也无意义）

### Apple Notes 实测校准（v3，全部是真实翻车）

- **空白**：普通空格的连续串被压成一个、行首缩进被剥；`&nbsp;` 能保住，读回时 Apple 输出的是普通空格。`<pre>` 里的空格和 tab 都保住
- **实体不带分号**：Apple 输出 `&quot` `&amp` `&lt` `&gt`，没有分号——正文里紧跟在这些字符后面的 `;` 会被标准解析器吞掉（真实丢过 `";"`）。拍平前把它们钉成数字实体（`flatten.apple_entities`）
- **表格吃掉后面一个空行**（确定性：0 或 1 个空行都存成同样形态）→ Apple 渲染时在表后多补一个空段
- **中文走苹方回退字体**：没有等宽（含中文的行内代码 → 字面），也没有斜体（含中文的斜体 → 字面）。中文为主的笔记里连 h3 的“斜体 + 18px”编码都会退化成 h2 → **h3 改为“粗体 + 下划线”**（下划线不是字体特征，任何文字都保得住；拍平器仍认旧编码）
- `<pre>` → 每行一个 monospaced 段落（`<font face="Courier"><tt>`）；行内 `<tt>`/`<code>` → Courier 字体段（`<font face="Courier"><span 12px>`）；但**整段只有 `<tt>` 的段落**会变成 monospaced 段落 → 整行只有行内代码时 Apple 端保持字面
- `<hr>` 刚写入时还在，定型后变成空行 → 字面；`<blockquote>` 直接被剥 → 字面；`<ol start>` 丢 start；`<ul>` 紧跟 `<ol>` 会并成一个 ul；`<li>` 里的 `<br>` 被并成空格；表格单元格首尾空格被修剪
- **异步定型**：新写入的笔记会在几秒到十几秒内被 Apple 重写一遍（修改时间不变），刚写完立刻读回的不一定是最终形态——审计必须等定型再读；引擎靠 seen_rev 把这种变化当“读法漂移”而非编辑
- JXA：`folders.byName` 遇到同名文件夹会随便挑一个（且不固定）→ bridge 现在拒绝不唯一的文件夹名（否则一翻转就像全部笔记被删）；`app.delete(folders.byName(...))` 对文件夹静默无效，要用 byId
- Evernote：ENML 原样存储（`-en-codeblock`、`<code>`、`<blockquote>`、`<ol start>`、嵌套列表、`&nbsp;` 都原样读回）；编辑器改写后的形态（如 `<li><div>…</div></li>`）拍平器已兼容

## 邮件转笔记（2026-10-15）

独立入口（类似 Evernote 的“邮件入笔记”）：发到某个指定地址的邮件，自动变成 Apple Notes **默认文件夹**里的一条笔记（可配置到别的文件夹）。不走 SYNC、不进 mapping、导入后不再同步。代码：`notesync/mailin.py`（解析 + 每轮步骤）、`mail_bridge.js`（Mail 侧）、`notes_create.applescript`（Notes 侧建带图笔记）。

- **接收：Mail.app + osascript 轮询，不用 IMAP。**Mail 本来就在后台同步该账户，我们只读它已经拉下来的邮件：零凭据、近零开销、不抢焦点、锁屏可跑。每轮 watch（以及菜单「立即同步」）在 sync 之后跑一次；Mail 没在运行就跳过（绝不拉起 Mail）
- **作用域**：INBOX 里其它邮件只批量取收件人列表比对（to/cc），Bcc 靠 Mail 内部求值的 `whose allHeaders contains` 过滤、只回传命中 id，再用投递头确认；只有命中的邮件才读 `source`
- **流水线**：AppleScript 只负责“筛出目标邮件 + 交出原始 MIME（写到临时文件）”，解析全在 Python `email`：主题→标题（去 Fwd:/转发: 前缀；正文首段重复标题时去掉），HTML 优先、否则纯文本（支持 format=flowed），HTML 清洗成白名单子集（布局表格拆开、样式脚本丢弃、链接目标以文字保留——Apple 会剥 href），cid 内嵌图 / data: 图 / 附件落临时目录后以 `file://` 引用；远程图片不抓取（隐私、追踪像素），保留成 `[image] (url)` 文字
- **去重**：Message-ID → `state.db` 的 `mail_import` 表（无 Message-ID 时用 接收时间+主题+发件人 的哈希）；失败重试 3 次后放弃并留在收件箱。顺序是“建笔记 → 记库 → 标记邮件”：宁可极小概率重复，不可丢
- **可见标记**：默认移到该账户的 `NoteSync` 邮箱（不存在就建）；也可只标已读 / 加旗 / 不动（`after_import`）。已导入但仍在收件箱（上次移动失败）的会重试移动
- **扫描窗口**：默认只看最近 1 天（`lookback_days`，用户要求“只看当天”）。但 Mail 的接收时间是**服务器时间**，Mac 睡眠 / 关机期间到的信醒来后已经“不是今天”——固定窗口会永久漏掉。所以窗口同时向回覆盖到“上次成功扫描 − 1 小时”（`state.db` 的 `meta.mail_last_scan`，上限 30 天）：平时只扫一天，停机后自动补扫。Mail 没开着、dry-run、带参数覆盖的一次性 `mail` 命令都不推进这个标记
- 配置 `mail_import`（见 config.example.json）：`enabled`、`address`（可多个）、`include_bcc`、`lookback_days`、`allowed_senders`（空 = 任何人）、`notes_account`/`notes_folder`、`after_import`、`done_mailbox`、`max_attachment_mb`
- CLI：`notesync mail [--dry-run] [--address A] [--folder F] [--after move|flag|read|none]`——一次性导入（无视 enabled），参数只覆盖本次；`--install-rule` / `--remove-rule` 管实时触发的 Mail 规则（见下）；`status` 多一行导入计数

### 实时触发（Mail 规则 → 触发文件 → kqueue）

- `notesync mail --install-rule` 在 Mail 里建规则「NoteSync mail import」：任一收件人包含导入地址 → 运行 `~/Library/Application Scripts/com.apple.mail/NoteSync Mail Import.scpt`。脚本只做一件事：`touch ~/Library/Caches/NoteSync/mail.trigger`（放 Caches 而不放 ~/Documents：规则脚本碰不到 TCC）。规则放在最前、不移动 / 不标记 / 不停止后续规则；`--remove-rule` 删规则和脚本
- watch 循环把 `time.sleep(300)` 换成 kqueue 等触发文件（`TriggerWait`）：空闲零开销；触发后等 2 秒（让 Mail 把信归档好）只跑邮件步骤，没导入到就 10 秒后再试一次；sync 节奏不变。sync 期间来的触发由 kqueue 排队（EV_CLEAR 合并），下一次等待立刻返回，不会丢
- 实测：发信 → 9 秒后 Mail 应用规则 → 12 秒后笔记进备忘录。5 分钟轮询保留兜底：Bcc（规则只认收件人）、规则没跑的情况（如 Mail 当时没开）都由它接住——规则坏掉期间的测试信正是轮询导入的

**真机怪癖（全是这次踩的）**
- **`run script` 的脚本路径**：脚本接口的 setter 只收“存在的绝对路径”并原样存进 `SyncedRules.plist`，但 Mail 运行规则时把它当成**相对脚本文件夹**的路径 → `<文件夹>/<绝对路径>` → ENOENT。规则照样“命中、应用”，只在 Mail 日志里留一行 `Failed to create an NSUserAppleScriptTask … No such file or directory`。getter 读回的就是那条拼错的路径（这是线索）。解法：在那条嵌套路径上也放一份脚本（安装命令自动做）
- **`header key` 条件不可脚本化**：记录里写 `rule type:header key` 会被解析成 `header` 属性，条件悄悄变成「发件人包含」；用原始码 `«constant ****thdk»` 一样；建好后再 `set rule type` / 删单个条件 → **Mail 直接崩溃**（-609）。所以规则只认收件人，Bcc 交给轮询
- 规则的“是否在本机启用”存在 `RulesActiveState.plist`（RuleId → bool），规则本体在 iCloud 同步的 `SyncedRules.plist`；别的 Mac 同步到这条规则但没有脚本，只会记一行错误
- 查 Mail 自己的日志才看得到规则执行结果：`/usr/bin/log show --predicate 'process == "Mail"'`（zsh 里 `log` 是内建命令，不写全路径什么也查不到）；关键 category `MFMessageRouter`

### Apple Notes 写图实测（真机，全部推翻过预判）

验证不驱动 UI：只读打开 `NoteStore.sqlite`（`file:...?mode=ro`），看 `ZICCLOUDSYNCINGOBJECT` 的 `ZTYPEUTI`、`ZMEDIA→ZFILENAME`，再把 `ZICNOTEDATA.ZDATA`（gzip 的 protobuf）解开数正文里的附件占位符 U+FFFC。

- `<img src="data:image/png;base64,…">` 写进 body：生成一个 **`public.data` 无类型附件**（显示成文件图标，不是图片），body 读回只剩空段落——mime、width/alt 怎么写都一样
- `make new attachment`（AppleScript `at end of attachments of n` / `at n` / `tell n`、JXA `make` 四种写法全试）：附件对象只有一个 `public.png`，但**正文里插了两个占位符 → 图显示两次**；脚本层读 `attachments` 也列两条
- `<img src="file://…">`：Notes 是沙盒 App，**没拿到文件读权限时导入成空图**（body 读回 `data:image/png;base64,(null)`，无 media 行）——放 `/var/folders/…/T`、`/private/tmp`、`~/Documents`、`~/Library/Caches` 全一样。读权限来自 Apple Event 里的文件引用：先 `tell application "Notes" to exists {POSIX file …}`（无副作用）把引用递过去，之后 file:// 图片导入为**类型正确（按扩展名推断 UTI）、位置正确、只一份、带原文件名**的附件；PDF、txt 同理。`get` 不行（AppleScript 本地解析，事件没发到 Notes）；JXA `app.exists(Path(…))` 报 -1728，所以建笔记这一步用 AppleScript
- 拿到权限后 create 是同步读文件的：建完立刻删临时文件不影响；文件名含中文 / 空格 / `#&` 用 `Path.as_uri()` 百分号编码即可
- `base64,(null)` 是可监测的失败信号：导入后读回一次，日志里报“N image(s) EMPTY”

### Mail 实测

- **`source()` 按“一字节一字符”（Latin-1）解码原始字节**：8bit 传输的 UTF-8 正文变成乱码（真实翻车：第一次端到端正文全是 `é®ä»¶…`，标题因为走 RFC 2047 编码反而正常）。写文件时按 Latin-1 编回即得原始字节，不能无损时才退回 UTF-8
- `messages.toRecipients.address()` 一个 Apple Event 返回整箱的嵌套数组，26 封全量扫描约 0.2 s；`whose({dateReceived: {_greaterThan: d}})` 可用，`_and` 可组合
- iCloud 给每封投递加 `Original-recipient: rfc822;<地址>`（Bcc 也有）；`message id` 不带尖括号，移动邮箱后不变（Mail 内部 `id` 会变）
- 建邮箱 `Mail.make({new: "mailbox", withProperties: {name}, at: account})`、移动 `Mail.move(m, {to: box})` 可用；刚删 / 刚移完立刻读 `messages.length` 可能还是旧值
- `Application("Mail").running()` 不发 Apple Event、不会拉起 Mail
- TCC：引擎宿主（NoteSyncBar）需要「自动化 → 邮件」。没授权 → -1743，日志提示去系统设置开；权限弹窗没人答时 osascript 会一直等 → 扫描 60 s 超时后暂停 30 分钟，免得每轮卡住 sync

## 全量 bootstrap 实战教训（2026-08-30，白名单前的一次性经历）

- EN 限速（across all tools）会打断长 bootstrap：错误隔离 + 65s 退避 + create 失败回滚（rollback 也要退避，否则留孤儿 → 下轮变 (2) 副本）
- 孤儿认领（claim_orphan：同 mapping+title+hash 且缺该端映射 → 认领而非发新 uuid）是防副本增殖的关键
- Apple `name` 是 body 首行的**截断版**：标题剥离必须前缀匹配，title 取未截断 body 首行（否则改名震荡+标题行逐轮堆积进正文）
- 标题空间必须对 sanitize_title 闭合：带 " (N)" 后缀的文件名若超 80 字符截断会破坏不动点 → 永久震荡

## Apple Notes 实测校准（2026-08-30）

- 写回可行（全项目最大风险解除）：set body 多行 `<div>` 结构保留、name 自动=首行、中文/emoji 正常
- 字节层不幂等但 md 层幂等：行尾 `<br>` 抖动、实体分号被吃（`&amp;`→`&amp`，html.unescape 兼容）——一切哈希在拍平后 md 层做
- 富文本碎片化：`<b>a</b><b>b</b>` 连写、`<b><br></b>` 孤行——拍平器已做标记平衡与垃圾清洗
- 纯格式差异不触发写回：端点富格式保留，语义变化才整篇拍平重写（比设计的"强制字面一致"更温和）
- JXA bridge（apple_bridge.js）批量取 list/read，跨轮缓存按 modificationDate 增量
- locked（passwordProtected）与含媒体（img/data:）笔记 frozen，不拉不推

## Evernote MCP 实测校准（2026-08-30，写代码前先读）

- 机器数据在 tools/call 结果的 `structuredContent`；text content 只是人类可读渲染
- `search_notebooks` 字段是 `label` 不是 `name`
- `create_note` 不收正文（空笔记 + `edit_note` append 补内容）
- 整篇替换 = `get_note` 取内层 ENML → `edit_note` `replace(find=完整内层, content=新片段)`；空笔记用 append
- 有内容的 body 无 `<?xml?>` prolog、空笔记有——解析容忍两种
- **search 索引是异步的，两头都会说谎**：新建的查不到（曾导致误判删除、险些丢数据）、刚删的还在（trash 幽灵被当新笔记拉下来）。防御：删除必须 `get_note` 主键二次确认（`fetch()` 探针）；`list()` 逐条核实 active 剔除幽灵
- `delete_note` = 进回收站（正合删除传播语义）；`updateSequenceNumber`(USN) 可做未来增量水位
- 待人工清理：多余笔记本 `NoteSync-技术_1`（label 匹配 bug 的产物，MCP 无删除笔记本工具）

## 核心决定速览

- canonical store = `iCloud Drive/SYNC/`（用户已建），端点里存 **literal md 源文本**（不渲染富格式）
- Evernote 走 agent 在用的官方 MCP（mcp-remote stdio + 共享 ~/.mcp-auth），零自建 OAuth
- 全部状态放 `~/Documents/NoteSync/`，「桌面与文稿」iCloud 同步自动备份（本机已确认开启）
- 身份 = uuid（文件 front-matter + state.db 映射），绝不按标题匹配 → 根治重复
- 变化判定 = 归一化内容 hash，mtime 只做触发
- 冲突 = diff3 三方合并；同行冲突新者胜、败方全文留底；修改胜过删除
- 含图片/附件的笔记整条排除（frozen），锁定笔记天然排除
