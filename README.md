# WkHelper

[![Python Version](https://img.shields.io/badge/Python-3.12%2B-blue?style=flat-square)](https://www.python.org/)
[![Package Manager](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/uv/main/assets/badge/v0.json&style=flat-square)](https://github.com/astral-sh/uv)
[![Code Style: Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json&style=flat-square)](https://github.com/astral-sh/ruff)

面向 AI Agent 与自动化工作流的 Headless 网课自动化 CLI，支持**雨课堂**（`yuketang.cn` / 高校定制平台）与**学堂在线**（`xuetangx.com`）。

---

## 核心特性

- **Agent 原生交互契约**
  - **stdout**：单行严格 JSON 响应信封（含 `schema_version`、`ok`、`data` 与自引导 `next_actions`）。
  - **stderr**：结构化 NDJSON 实时事件通道（`video.progress`、`homework.progress`、`http.trace` 等），杜绝控制字符污染。
- **全平台多层章节树支持**
  - 递归遍历解析单层、多层嵌套大纲（章 / 节 / 小节 / 单元），精确识别全量叶子任务（视频、音频、课堂实录、作业、测验与期末考试）。
- **字体反混淆引擎**
  - 内置基于字形矢量特征的动态字体反混淆解析，自动还原雨课堂作业与测验题目中的加密字形与公式。
- **拟人化防风控体系**
  - **心跳抖动**：视频心跳间隔支持随机抖动（`--jitter`），消除周期性机器指纹。
  - **切换冷却**：跨视频支持设置间隔等待（`--video-delay`）。
  - **作业节流**：支持自定义并发（`--concurrency`，默认单线程）、逐题提交延迟（`--delay` / `--delay-jitter`）与乱序提交（`--random-order`）。
  - **浏览器指纹**：默认伪装 Chrome 的 TLS/JA3 与 HTTP/2 指纹（`WKHELPER_IMPERSONATE` 可切换目标或 `none`），UA 与指纹保持一致。
  - **网络出口**：全局支持指定代理通道（`--proxy`）与自定义浏览器标头（`--user-agent`，指定后会关闭指纹伪装）。
- **安全凭据存储**
  - 接入操作系统安全保管库（Windows Vault / macOS Keychain / SecretService），不以明文存储会话秘密；支持系统凭据或纯内存会话（`--credential-mode session`）。
- **完备的机器契约自省**
  - 提供 `wkhelper schema`，供 LLM Agent 按需自省命令规范、参数定义、副作用等级与调用示例。

---

## 架构概览

```mermaid
flowchart LR
    A[AI Agent / 终端用户] -->|CLI 参数| B[WkHelper CLI]
    B --> C{Core 调度引擎}
    C -->|凭据管理| D[CredentialStore / Keyring]
    C -->|参数化视频引擎| E[Video Engine]
    C -->|拟人化提交| F[Homework Engine]
    C -->|反混淆导出| G[Deobfuscator & Exporter]
    E --> H[平台适配层: Yuketang / XuetangX]
    F --> H
    G --> H
    H -->|stdout| I[标准 JSON 信封]
    H -->|stderr| J[结构化 NDJSON 事件流]
```

---

## 安装与快速上手

### 环境要求

- Python `>= 3.12`
- 包管理器任选其一：[`uv`](https://github.com/astral-sh/uv)（推荐）、[`pipx`](https://pipx.pypa.io/)、`pip`

### 安装

项目尚未发布到 PyPI，请从 GitHub 仓库安装。

```bash
uv tool install --force git+https://github.com/L-1124/WkHelper.git
uvx --from git+https://github.com/L-1124/WkHelper.git wkhelper status
pip install git+https://github.com/L-1124/WkHelper.git
```

安装后自检：

```bash
wkhelper --version     # 0.1.0
wkhelper status        # 本地凭据后端与已存账号
```

---

## 常用命令与工作流

### 1. 账号认证 (`auth`)

```bash
# 微信扫码登录并自动在浏览器中打开二维码页面（支持 ykt / xtzx）
wkhelper auth login -p ykt -m qrcode --display browser --open-browser

# 导入 Cookie 文件登录（无图形环境）
wkhelper auth login -p xtzx -m cookie --cookie-file cookies.json

# 验证当前凭据有效性
wkhelper auth status -p ykt

# 查看本地存储的全部账号摘要（绝不输出敏感 Cookie）
wkhelper auth list
```

凭据保存或删除遇到数据库连接、执行或提交失败时，会回滚事务并补偿恢复旧秘密；若秘密补偿本身失败，错误会保留数据库故障的因果链。

### 2. 课程查询 (`course`)

```bash
# 列出在学课程（返回包含 classroom_id 的唯一引用）
wkhelper course list -p ykt --limit 10

# 查看指定课程详细信息与元数据大纲
wkhelper course inspect -c 1966245:31318907
```

### 3. 视频学习与计划编排 (`video`)

```bash
# 查看待完成视频列表
wkhelper video list -c 1966245:31318907 --status pending

# 检查单视频进度与参数约束
wkhelper video inspect -c 1966245:31318907 -v 75369628

# 开启防风控参数执行视频学习（预览）
wkhelper video learn -c 1966245:31318907 -v 75369628 --jitter 0.2 --dry-run

# 正式执行视频推进（写操作强制要求 --confirm）
wkhelper video learn -c 1966245:31318907 -v 75369628 --jitter 0.2 --confirm

# 基于 JSON 计划文件批量执行，并记录断点检查点
wkhelper video learn --plan-file plan.json --checkpoint-file cp.json --confirm
```

> [!TIP]
> 批量执行支持断点续学。追加 `--resume-from cp.json` 会跳过满足本次完成阈值的记录；提高阈值后，旧的部分完成记录会继续执行。`--total-timeout` 限制整次执行，包括已启动任务及视频切换等待。
> 视频截帧缓存按实际认证平台与用户隔离；并发请求等待同一会话时不会阻塞事件循环，清理命令遇到被占用的会话会跳过。

### 4. 作业与题目检索 (`homework` / `question`)

```bash
# 查询未完成作业列表
wkhelper homework list -c 1966245:31318907 --status pending

# 导出整门课的作业及测试题目为 Markdown（供 LLM 分析解题）
wkhelper questions export -c 1966245:31318907 -o ./questions.md

# 查看单道题目的完整反混淆题干、选项与作答结果
wkhelper question get -c 1966245:31318907 -w 84704898 -q 73577720

# 模拟真实人类作答节奏提交作业（单线程 + 依据题目难易度动态浮动间隔）
wkhelper homework submit -c 1966245:31318907 -w 84704898 \
  --answers '{"73577720": ["C"], "73577724": ["B"]}' \
  --concurrency 1 --delay 8.0 --delay-jitter 3.0 --random-order --confirm
```

> [!NOTE]
> `homework` / `questions` 仅覆盖章节作业（`leaf_type=6`）；期中、期末等考场由 `exam` 命令族处理，两者不再混列。
> 导出按作业 ID 区分同名节点。任一作业抓取失败时整次导出报错，不发布不完整文件；作业与考试导出均拒绝覆盖既有目标，包括导出期间由其他进程创建的文件。

### 5. 资料阅读与图文题 (`article`)

```bash
# 查看课程资料与图文任务列表
wkhelper article list -c 1966245:31318907 --status pending

# 标记指定资料节点为已读已完成（写操作要求 --confirm）
wkhelper article read -c 1966245:31318907 -a 84705076 --confirm
```

资料标记必须获得平台成功确认；平台拒绝或响应未确认成功时输出错误信封，退出码为 6，不宣称已完成。

### 6. 讨论题互动打卡 (`discussion`)

```bash
# 查询全课讨论题及参与发言状态
wkhelper discussion list -c 1966245:31318907 --status pending

# 查看特定讨论题要求与教师引导题干
wkhelper discussion inspect -c 1966245:31318907 -d 84704891

# 提交讨论发言完成平时分打卡考核
wkhelper discussion reply -c 1966245:31318907 -d 84704891 \
  --content "今天的三件好事：第一，掌握了积极心理学的新视角..." --confirm
```

### 7. 公告与通知 (`notice`)

```bash
# 查询课程公告/通知节点及已读状态
wkhelper notice list -c 1966245:31318907 --status pending

# 读取公告正文（Markdown）、发布者与发布时间
wkhelper notice inspect -c 1966245:31318907 -n 84705560
```

### 8. 考场与考试 (`exam`)

```bash
# 查询课程内的期中/期末考试及截止时间
wkhelper exam list -c 1966245:31318907

# 查看考试封面、考场须知与监考开关
wkhelper exam inspect -c 1966245:31318907 -e 84705331

# 预览进入考场（不申请令牌）
wkhelper exam start -c 1966245:31318907 -e 84705331 --dry-run

# 进入考场：默认拉起前台可见浏览器保持考场页面
wkhelper exam start -c 1966245:31318907 -e 84705331 --confirm

# 读取整卷题目（只读）或导出为 Markdown
wkhelper exam paper -c 1966245:31318907 -e 84705331
wkhelper exam export -c 1966245:31318907 -e 84705331 -o ./exam.md

# 在考场页面中模拟点击/输入完成单题作答（需保持浏览器会话）
wkhelper exam answer -c 1966245:31318907 -e 84705331 -q 9001 --answer A --confirm

# 只读诊断：封面/refresh_time/暂存作答/成绩接口状态
wkhelper exam status -c 1966245:31318907 -e 84705331

# 结束考场浏览器会话
wkhelper exam close
```

> [!IMPORTANT]
> - 作答只通过前台真实浏览器页面的模拟点击与输入完成，不存在绕过浏览器的作答接口；交卷必须由本人在浏览器中核对后点击。
> - `exam answer` 优先核验页面自身的 `answer_problem` 请求内容与有效业务成功码（通过 CDP 抓取，不额外发请求）；HTTP 200、HTML、空响应或缺失成功码均不能证明保存成功。未捕获到请求或响应证据未知时，只回读一次 `/exam_room/cache_results` 兜底；仍无法确认则返回 `saved: false`。
> - 平台明确拒绝或页面实际提交内容与预期不一致（例如选项未绑定），会直接判 `saved: false`，不会被旧暂存回读洗白。
> - 判断题选项键是 `true`/`false`，`--answer 正确` 会自动映射，也接受直接传 `true`。
> - 在线监考、人脸/实名核验、网页截屏与线下机房等**实际启用**的监考项会锁定全部自动化入场与作答，只保留只读查询。
> - `app_capture_screen`、`app_front_random_snap` 属于 App 端从属配置，未开启在线监考时不视为已启用监考。
> - 强监考锁定只阻止自动化写操作，`exam inspect` 等只读查询始终可用。

### 9. 契约自省 (`schema`)

```bash
# 查询所有命令列表与摘要索引
wkhelper schema

# 展开特定命令的完整参数契约、类型与示例
wkhelper schema --path "homework submit" --full
```

---

## 防风控参数配置

| 模块 | 参数 | 默认值 | 作用说明 |
|---|---|---|---|
| **全局** | `--proxy <url>` | 无 | 路由全局网络流量通过 HTTP / SOCKS5 代理出口 |
| **全局** | `--user-agent <ua>` | 伪装目标自带 | 指定真实浏览器 UA；与指纹伪装互斥，指定后回退为 libcurl 默认指纹 |
| **全局** | `--format <json\|md>` | 按 TTY 推断 | 强制输出格式，非 TTY 下默认 `json` |
| **全局** | `--credential-mode <system\|session>` | `system` | 凭据写入系统保险库，或仅驻留内存 |
| **全局** | `--data-dir <dir>` | 平台默认目录 | 凭据元数据与检查点数据目录 |
| **全局** | `--cookie-file <path>` | 无 | 会话 Cookie 文件，需配合 `--credential-mode session` |
| **作业提交** | `--concurrency <N>` | `1` | 严格限制并发提交数，默认单线程顺序提交 |
| **作业提交** | `--delay <sec>` | `8.0` | 每个提交任务获得并发槽位后等待；默认并发 1 时逐题保持间隔，并发大于 1 时不承诺全局请求间隔 |
| **作业提交** | `--delay-jitter <sec>`| `3.0` | 提交间隔浮动范围，实际间隔为 `delay ± jitter`，消除固定周期机械特征 |
| **作业提交** | `--random-order` | `false` | 随机打乱题目提交次序，打破从第 1 题顺序作答的机械规律 |
| **单题/考试** | `--delay <sec>` | `0.0` | 提交或作答前思考等待秒数，建议依据难度动态传入（简单题 5~15s，多选/计算 15~35s，主观题 45~90s） |
| **单题/考试** | `--delay-jitter <sec>`| `0.0` | 思考等待随机浮动范围，消除固定机器节奏 |
| **视频学习** | `--jitter <ratio>` | `0.2` | 心跳间隔随机浮动比例（如 `1.5s ± 20%`） |
| **视频学习** | `--video-delay <sec>` | `3.0` | 批量学习时视频切换之间的冷却等待秒数 |

> 全局参数可置于子命令之前或之后，例如 `wkhelper --format md course list` 与
> `wkhelper course list --format md` 等价；同名命令级选项（如 `auth login --cookie-file`）优先归属该命令。

---

## 调试模式 (`--debug`)

追加全局 `--debug` 参数可激活面向 Agent 的结构化调试体系：

```bash
wkhelper --debug course inspect -c 97330:29593706
```

- **错误堆栈诊断**：错误信封中的 `error.details` 自动填充 `exception_type` 与结构化 `traceback` 数组；非调试模式下严格隐藏内部堆栈。
- **实时网络追踪**：stderr 通道以 NDJSON 输出 `http.trace` 追踪流（含请求方法、URL、响应状态码与微秒耗时），保持标准格式不被文本打乱。

---

## 开发与贡献

```bash
# 同步依赖并激活虚拟环境
uv sync

# 代码格式化与质量检查
uv run ruff check . --fix
uv run ruff format .
uv run pyrefly check

# 运行单元测试
uv run pytest -q
```

---

> [!WARNING]
> 本项目仅供技术研究、接口逆向学习与自动化流程测试使用。请使用者严格遵守各在线教育平台的用户协议与学校相关规定，切勿用于作弊或任何违规用途。
