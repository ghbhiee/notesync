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
