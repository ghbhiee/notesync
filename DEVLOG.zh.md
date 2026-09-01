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
