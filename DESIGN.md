# NoteSync 架构设计 v1

日期：2026-08-29 · 状态：设计定稿，待实现
作者场景：Mac 常开做同步 hub；Windows 用 Evernote 客户端；iPhone 用 Apple Notes / 文件 App；三端看到同样的纯文本 markdown。

## 1. 一句话

以 iCloud Drive 里的一个 md 文件夹为**唯一真相源**（canonical store），Mac 上的同步引擎把 Apple Notes 与 Evernote 的内容**强制归一化为纯文本 markdown** 并保持三端一致；内部用 git 提供历史与三方合并，Swift 菜单栏 App 负责监控与冲突处理。

## 2. 目标与非目标

**目标**
- 纯文本（markdown 源码）三端双向同步：文件系统 ⇄ Apple Notes ⇄ Evernote。
- 星型拓扑：只有 Mac 上的引擎读写各端，端点之间从不直接同步。
- 增量：只处理变化的笔记；以**归一化内容 hash** 判定变化，mtime 只做触发提示。
- 冲突用三方合并（diff3）自动吸收，真冲突有明确规则且**绝不静默丢字**。
- Mac 关机时各云（iCloud / Evernote / Google Drive 镜像）各自持有全量副本，其他设备照常可读可改；Mac 开机后引擎追平积压变更。

**非目标（明确放弃）**
- 多媒体与附件：含图片/附件/绘图的笔记**整条排除**在同步之外（见 §8 frozen 规则）。
- 富格式保真：端点里的富格式会被拍平成 md 源文本（用户已确认接受）。
- 锁定笔记（AppleScript 读不到，天然排除）。
- 实时性：分钟级即可。
- Windows 端组件：零安装，Windows 只用 Evernote 官方客户端。

## 3. 拓扑

```
                    ┌────────────────────────────┐
                    │  Mac hub（引擎，launchd）   │
                    │  git 引擎 + state.db        │
                    └──────┬──────┬──────┬───────┘
                           │      │      │
        AppleScript 适配器 │      │      │ MCP 适配器（复用 evernote-sync/enmcp）
                           ▼      │      ▼
                  Apple Notes     │   Evernote 云 ──► Windows/手机 Evernote 客户端
                （iCloud 自己同步  │
                  到 iPhone）     ▼
                     canonical store：iCloud Drive/SYNC/（md 文件）
                           │              ──► iPhone 文件 App / 其他 Mac
                           ▼
                （可选）单向镜像拷贝到 Google Drive 客户端文件夹
```

要点：
- **iCloud Drive 与 Google Drive 不是端点，是运输层。** canonical store 本身就放在 iCloud Drive；Google Drive 只是一个可选的单纯文件夹镜像任务（单向拷贝），不参与合并。
- 真端点只有两个需要写适配器：Apple Notes、Evernote。文件端点＝canonical store 自身（用户可直接改文件）。

## 4. canonical 格式：literal markdown 纯文本

**核心决定：端点里存的就是 md 源码本身，不做富格式渲染。**
Apple Notes / Evernote 里看到的是 `**加粗**`、`- 列表` 这样的字面文本。收益：转换天然幂等、回声几乎免费、diff3 直接作用在用户看到的内容上。

**归一化写回（M3 实测后修正：实现自然给出更温和且更优的行为）**：所有比较都发生在拍平后的 md 层，因此**纯格式差异不触发写回** —— 用户在 Apple/Evernote 里加粗、标题含 `/` 等，只要拍平后语义不变，端点保持自己的富格式显示不被打扰；只有内容（语义）变化才整篇重写为纯文本 md。三端语义恒一致，字面在首次内容修改后趋同。原设计的"强制字面一致"描述由此放宽。

**归一化写回（原始描述）**：用户在端点用富文本按钮加了格式（如 Evernote 里点了加粗，ENML 出现 `<b>`），引擎拉取时拍平为 md（`**…**`），若与该端点现存内容不同，则把拍平后的纯文本**写回该端点**，保证三端字面一致。每条笔记每轮最多写回一次，且有"无进展守卫"（写回后再拉取仍不等于 canonical → 记 fidelity 警告并以拉回内容为新基线，不再循环）。

**归一化函数 N(x)**（每次比对、哈希前执行）：
- CRLF/CR → LF；NBSP( ) → 空格；去每行行尾空白；文件尾恰好一个 `\n`。
- 不合并空行、不动缩进（保守，避免误伤代码块与手工排版）。

**拍平规则（HTML/ENML → md）**：

| 端点结构 | md |
|---|---|
| `<b>/<strong>` | `**…**` |
| `<i>/<em>` | `*…*` |
| `<h1..h3>` | `#` `##` `###` |
| `<ul><li>` | `- ` |
| `<ol><li>` | `1. ` |
| `<a href>` | `[text](url)` |
| checklist（若可识别） | `- [ ]` / `- [x]` |
| `<div>/<p>/<br>` | 换行 |
| 其余标签 | 剥壳取文本 |

**标题规则**：
- 标题的真相 = **文件名**（去 `.md`）。
- Evernote：映射到独立的 title 字段，body 不含标题行。
- Apple Notes：其标题恒等于正文第一行，因此推送时 body 第一行 = 文件名，其后为内容；拉取时第一行剥离为标题。
- 文件名清洗：`/ \ :` 等 → `-`，去首尾空白，长度上限 80，同名冲突加 ` (2)` 后缀。改名 = 标题变更，双向传播（身份由 uuid 保持，见 §5）。

## 5. 身份与映射（去重的根治）

重复只会来自"按标题/文件名匹配"。本系统从第一天起用稳定 ID：

- 每条逻辑笔记一个 **uuid**，写在 md 文件 front-matter（仅文件层有 front-matter，**绝不写进端点正文**）：

  ```markdown
  ---
  ns: 8f3a1c…            # uuid
  ---
  正文…
  ```

- 端点原生 ID 做映射键：Apple Notes 的 `x-coredata://…/ICNote/pNNN`（已实测稳定）、Evernote note GUID。
- 手工新建的无 front-matter 文件：首轮同步时补写。

**state.db（SQLite，放 `~/Documents/NoteSync/`，见 §6）**

```sql
CREATE TABLE notes (
  uuid        TEXT PRIMARY KEY,
  mapping     TEXT NOT NULL,     -- 属于哪条 mapping（§10）
  title       TEXT,
  rel_path    TEXT,              -- 相对 canonical store
  base_hash   TEXT,              -- 上次共识内容 N(x) 的 hash
  base_commit TEXT,              -- 共识版本所在 git commit
  status      TEXT DEFAULT 'active'  -- active | tombstone | frozen
);
-- 每端点一行（M1 实现时把设计稿的固定 apple_*/en_* 列泛化成通用表，
-- 测试替身与未来端点共用同一条代码路径）
CREATE TABLE ep_map (
  uuid      TEXT NOT NULL,
  ep        TEXT NOT NULL,          -- 'apple' | 'en' | 测试替身名
  native_id TEXT NOT NULL,          -- x-coredata URI / note GUID / 文件名
  base_hash TEXT,                   -- 上次同步后该端归一化 hash（回声抑制）
  seen_rev  TEXT,                   -- 端点侧变更触发水位
  PRIMARY KEY (uuid, ep)
);
CREATE TABLE oplog (
  ts REAL, uuid TEXT, action TEXT, detail TEXT   -- 每次读写留痕，GUI 展示
);
```

## 6. git 引擎与状态存放

**引擎**
- `GIT_DIR=~/Documents/NoteSync/repo.git`，`GIT_WORK_TREE=<canonical store>` —— **SYNC 文件夹里零隐藏文件**（用户硬性要求：目录里不能有缓存文件）。
- 每轮产生新共识即 commit（信息注明来源端），历史查看/回滚免费。
- 三方合并直接 `git merge-file --diff3`（base = `base_commit` 中该文件）。

**状态存放（用户拍板：直接放 Documents，macOS 自己同步）**
- 全部状态（repo.git、state.db、conflicts/、config.json、日志）放 `~/Documents/NoteSync/`。本机已确认「桌面与文稿」iCloud 同步开启，macOS 自动把整个目录备份到 iCloud —— 不再自建快照/备份机制。
- 因此 `GIT_DIR=~/Documents/NoteSync/repo.git`（SYNC 内容目录里依旧零缓存文件）。
- 引擎在两轮同步之间保持**静默态**：每轮收尾做 sqlite checkpoint 并关闭连接，让 iCloud 上传到的绝大多数时刻都是一致状态；活跃周期那几秒内的云副本可能写了一半（已知残余风险，接受）。
- 安装时提醒一次：Finder 对 `~/Documents/NoteSync` 右键「保留已下载项」，防"优化储存"驱逐 git objects 导致引擎读不到。
- 兜底认知：内容本体在 SYNC/（iCloud）、Evernote 云、Apple Notes 三处各有全量副本；state/git 只是元数据，最坏走一次 §11 bootstrap 去重重建。**任何单点损坏都不会让项目完蛋。**

## 7. 同步循环

触发：canonical store 上的 FSEvents（防抖 5s）＋ 定时轮询（Apple 默认 120s、Evernote 默认 300s）＋ GUI 手动。

每轮：

1. **变更侦测（增量）**
   - 文件：FSEvents 命中 + 周期全量 walk（兜 iCloud 后台落盘）；候选文件读前确保 iCloud 已物化（未下载则触发下载并跳到下轮）。
   - Apple：AppleScript 批量取 scoped 文件夹内 `(id, name, modification date)`，> `apple_seen_mod` 者才取 body。
   - Evernote：`search_notes` 按 updated 水位增量（复用 en_sync 模式）。
2. **归一化**：对每条候选笔记算出至多三个当前版本 `V_file / V_apple / V_en`（各自 N(x) 后），基线 `B` 取自 git。
3. **裁决**
   - 与 B 全等 → 无事。
   - 恰一端变 → 该版本即新共识 C。
   - 多端变 → 折叠合并：`M = diff3(B, V1, V2)`，再 `diff3(B, M, V3)`；干净则 C = M；有同行冲突走 §8 策略。
4. **落地**：C 写文件（如变）→ git commit → 推送到内容 ≠ C 的端点。
5. **回声抑制**：每次推送后立刻取回该端点内容，`N(取回)` 的 hash 存为该端 `*_base_hash`，并更新水位。下轮该端 hash == base → 判无变化。

## 8. 冲突、删除与排除

| 情形 | 处理 |
|---|---|
| 单端修改 | 直接成为新共识 |
| 多端修改，diff3 无重叠 | 自动全部合并（"增加、更新优先"是它的天然行为） |
| 同一行两端都改 | mtime/updated 较新方胜；**败方全文**存入 `~/Documents/NoteSync/conflicts/<uuid>-<ts>.md`，GUI 冲突收件箱亮灯 |
| 一端删除，他端自基线未改 | 传播删除：Apple → 最近删除；Evernote → 回收站；文件 → 从文件夹移除（git 历史可找回）。写 tombstone |
| 一端删除，他端已修改 | **修改胜过删除**：复活被删端（防丢内容） |
| 两端各自新建同名 | 两个 uuid 共存，文件名加 ` (2)`，不自动合并 |

- **tombstone**：已删笔记在 state.db 标记，防止未追平的端把它当新笔记复活。
- **frozen（媒体排除）**：拉取时检测到 Apple body 含 `<img`/附件、或 Evernote note 有 resources → 该笔记标记 frozen，**不拉不推**（写回会毁掉媒体，宁可不碰），GUI 列出清单。笔记后来清掉媒体则自动解冻。

## 9. 端点适配器

**files**：即 canonical store。注意 iCloud 驱逐（Optimize Storage）——读前检查物化状态。

**apple**（AppleScript，走 Apple Events，不抢焦点、锁屏可用）：
- 读：body 即 HTML（已实测）；拍平 → md。
- 写：md → 逐行 `<div>…</div>`（空行 `<div><br></div>`），第一行为标题。**写回带格式内容的实际效果是 M3 的第一个实验**，未验证前 Apple 端只读。
- 排除：锁定笔记不可见；frozen 规则见 §8。

**evernote**（**直接复用 agent 在用的官方 Evernote MCP，不自建 OAuth**）：
- 传输：引擎以 stdio 子进程方式起 `npx -y mcp-remote https://mcp.evernote.com/mcp` —— 与本机所有 agent 客户端**同一条命令、同一份 `~/.mcp-auth` 凭据**，一次授权全家共享，engine 里零 OAuth 代码（`enmcp/oauth.py` 弃用，仅留作兜底）。
- 复用早期原型的 intent→tool 参数映射与 md⇄note body 转换层，换掉其网络层。
- 同步用的笔记本与用户既有笔记本组隔离，避免与其他自动化互相污染。
- 注意 MCP 参数 key 需 `schema` 校准（en_sync README 的坑）。

## 10. 目录与配置

canonical store：`~/Library/Mobile Documents/com~apple~CloudDocs/SYNC/`（即 iCloud Drive/SYNC，用户已创建）。

`config.json`：

```json
{
  "store": "iCloud Drive/SYNC",
  "state": "~/Documents/NoteSync",
  "poll_apple_seconds": 120,
  "poll_evernote_seconds": 300,
  "gdrive_mirror": null,
  "mappings": [
    { "name": "tech",  "folder": "技术",  "apple_folder": "技术",  "en_notebook": "NoteSync-技术" },
    { "name": "main",  "folder": "Notes", "apple_folder": "Notes", "en_notebook": "NoteSync-Notes" }
  ]
}
```

- 一条 mapping = 三端各一个平级容器（文件夹/Apple 文件夹/EN 笔记本），mapping 内**平铺不嵌套**（沿用 en_sync v1 简化）。
- 不在 mapping 里的 Apple 文件夹 / EN 笔记本完全不被触碰。

## 11. 首次导入（bootstrap）

1. 按 mapping 拉取 Apple + Evernote 全量 → 拍平为 md。
2. 去重配对：**标题相同且归一化 hash 相同** → 认作同一条，共用 uuid；标题同 hash 异 → 双份保留加后缀（宁重勿丢，人工清）。
3. frozen（含媒体）与锁定笔记入排除清单，GUI/报告展示。
4. 全部落文件、建映射、打首个 git commit——此后进入常规循环。

## 12. 组件与技术选型

| 组件 | 选型 | 理由 |
|---|---|---|
| 同步引擎 | Python 3（无头 CLI + daemon 模式） | 直接复用 enmcp 的 OAuth/MCP 层；引擎可脱离 GUI headless 测试 |
| 历史/合并 | git（外置 GIT_DIR） | diff3、原子性、历史免费 |
| Apple 适配 | osascript（argv 传参，不拼字符串） | 已实测可行 |
| Evernote 传输 | mcp-remote stdio 子进程，共享 `~/.mcp-auth` | 与 agent 同一授权，零 OAuth 代码 |
| 状态/备份 | `~/Documents/NoteSync/`，「桌面与文稿」iCloud 同步自动兜底 | 用户拍板；轮间静默保一致 |
| 驻留 | launchd（daemon: `notesync watch`） | Mac 常开即同步 |
| GUI | Swift 菜单栏 App（固定自签证书，沿用 HyperVibe/WinMan 套路） | 状态灯、mapping 开关、冲突收件箱、oplog 流水、暂停/手动同步；只读 state.db + 调 CLI，不含同步逻辑 |

## 13. 幂等性测试清单（M1/M3 关卡，先写测试再写转换）

1. `md → 写文件 → 读回 → N(x)` 恒等。
2. `md → Apple body HTML → 拍平 → md` 二轮起不动点（第一轮允许有损，第二轮必须恒等）。
3. `md → ENML → 拍平 → md` 同上。
4. 覆盖样例：中文＋emoji 标题、空行连续、`- [ ]` 清单、代码围栏、超长行、行尾空格、仅改 mtime 不改内容（应判无变化）、标题改名往返、删除→tombstone→另一端复活场景。
5. 回声实验：推送后连续 3 轮空转，state.db 无写入、端点无写回。

## 14. 风险（按疼痛排序）

1. **Apple Notes 写回**是全链最脆一环，未实测。M3 首个实验；最坏退路：Apple 端降级"只读拉取＋纯文本可写"。
2. Apple checklist 在 AppleScript body 里的真实形态未知（M3 实测决定拍平规则）。
3. iCloud 驱逐/延迟落盘 → 读到半旧文件；靠物化检查＋归一化 hash 兜底。
4. Evernote MCP 限速/参数漂移 → 轮询保守（300s），schema 校准脚本保留。
5. mcp-remote 凭据按版本号分目录存，npx 升级会"掉授权"（本机踩过，见 ~/.mcp-auth 现状）；引擎钉住与 agent 一致的调用方式，掉线时提示重跑任一 agent 客户端的登录即可，不自建修复逻辑。
6. 标题=文件名耦合：两端同时改标题与内容时改名与合并交织，M1 需专门测试。

## 15. 里程碑

- **M0** 本设计。✅
- **M1** 核心引擎：store + git + state.db + 文件端点 + diff3 + tombstone + 测试套件（不接任何笔记软件）。
- **M2** Evernote 适配器：mcp-remote stdio 直连（共享 agent 授权）+ 复用 enmcp 的 api/convert，试点 `NoteSync-技术` 笔记本。
- **M3** Apple Notes 适配器：先只读导出；写回单独实验（试点「技术」文件夹，现仅 1 条笔记）通过后放开双向。
- **M4** 全量 mapping + bootstrap + launchd 驻留 + Google Drive 镜像。
- **M5** Swift 菜单栏 GUI。
