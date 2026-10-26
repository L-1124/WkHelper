# WkHelper

[![Python Version](https://img.shields.io/badge/Python-3.12%2B-blue?style=flat-square)](https://www.python.org/)
[![Package Manager](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/uv/main/assets/badge/v0.json&style=flat-square)](https://github.com/astral-sh/uv)
[![Code Style: Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json&style=flat-square)](https://github.com/astral-sh/ruff)

面向 AI Agent 与自动化工作流的网课自动化 CLI 工具，支持**雨课堂**、**学堂在线**与**中国大学 MOOC**。提供全流程非交互操作、结构化输出通道与反作弊风控防护。

---

## 支持平台

| 平台代码 | 平台名称 | 官网 | 支持能力 |
|---|---|---|---|
| `ykt` | 雨课堂 | `yuketang.cn` / 高校定制平台 | 账号登录、课程大纲、视频学习/抽帧、作业与题目检索/提交/导出、资料、讨论、考试 |
| `xtzx` | 学堂在线 | `xuetangx.com` | 账号登录、课程大纲、视频学习/抽帧、作业与题目检索/提交/导出、资料、讨论、考试 |
| `icourse163` | 中国大学 MOOC | `icourse163.org` | 账号登录、课程列表、多层章节大纲与进度查询，视频/作业/资料/讨论/考试只读列表 |

> [!NOTE]
> 中国大学 MOOC（`icourse163`）目前支持账号登录、课程大纲与进度查询，以及各模块只读列表；视频学习推进与作业提交等写操作暂未接入。

---

## 核心特性

- **Agent 优先交互契约**
  - **stdout**：单行严格 JSON 响应信封（含 `schema_version`、`ok`、`data` 与自引导 `next_actions`），方便脚本与 LLM 流水线解析。
  - **stderr**：结构化 NDJSON 实时事件流（`video.progress`、`homework.progress`、`http.trace` 等），杜绝控制字符污染。
  - **机器自省**：内置 `wkhelper schema`，供 Agent 自行查询命令规范、参数约束与调用示例。
- **浏览器驱动登录与安全存储**
  - 基于 Chrome DevTools Protocol (CDP) 打开官方登录页，用户手动完成登录后自动捕获核心凭据并核验。
  - 凭据使用 Fernet 认证加密存储于本地 SQLite，主密钥由操作系统安全保管库（Windows Vault / macOS Keychain / SecretService）托管；支持纯内存会话模式（`--credential-mode session`）。
- **多层大纲解析与反混淆**
  - 递归遍历解析单层与多层嵌套大纲（章 / 节 / 小节 / 课时），统一识别视频、课件、随堂测验、作业与考试节点。
  - 内置基于字形矢量特征的动态字体反混淆引擎，自动还原雨课堂作业与测验题目中的加密字形与公式。
- **拟人化风控与流量防护**
  - **节奏控制**：视频心跳支持随机浮动（`--jitter`），跨视频支持冷却等待（`--video-delay`）。
  - **作业节流**：支持并发控制（`--concurrency`）、逐题提交等待（`--delay` / `--delay-jitter`）与乱序提交（`--random-order`）。
  - **指纹伪装**：默认模拟 Chrome 的 TLS/JA3 与 HTTP/2 指纹，支持 HTTP/SOCKS5 代理出口（`--proxy`）。
- **无头视频抽帧**
  - 依托浏览器 CDP 对网页播放器直接提取视频帧，支持指定时间戳、采样步长或均匀采样，无需本地安装 ffmpeg 或音视频解码库。

---

## 环境要求与安装

### 前置要求

- **Python** `>= 3.12`
- **本地浏览器**：Google Chrome 或 Microsoft Edge（用于浏览器登录及视频抽帧）
- **包管理器**：推荐 [`uv`](https://github.com/astral-sh/uv)，亦支持 `pipx` 或 `pip`

### 安装方法

```bash
# 推荐：使用 uv 安装为全局 CLI 工具
uv tool install --force git+https://github.com/L-1124/WkHelper.git

# 或者使用 pipx
pipx install git+https://github.com/L-1124/WkHelper.git

# 或者使用 pip
pip install git+https://github.com/L-1124/WkHelper.git
```

安装后验证环境：

```bash
wkhelper --version
wkhelper status
```

---

## 快速上手

```bash
# 1. 登录账号（自动启动浏览器窗口，登录成功后自动捕获并加密保存）
wkhelper auth login -p ykt

# 2. 列出在学课程
wkhelper course list -p ykt

# 3. 查看指定课程详情与大纲结构
wkhelper course inspect -c 1966245:31318907
```

---

## 常用命令与工作流

### 1. 账号认证 (`auth`)

```bash
# 启动浏览器完成官方登录（支持 ykt / xtzx / icourse163，默认方式）
wkhelper auth login -p ykt

# 指定登录方式与超时时间
wkhelper auth login -p xtzx -m browser --timeout 180

# 导入 Cookie 文件登录（适用于无图形环境或自动化 CI）
wkhelper auth login -p icourse163 -m cookie --cookie-file cookies.json

# 检查已保存凭据的在线有效性
wkhelper auth status -p ykt

# 查看本地存储的账号列表（绝不输出明文 Cookie）
wkhelper auth list

# 切换默认账号（该平台只有一个账号时可省略 -a）
wkhelper auth switch -p icourse163 -a 123456

# 后续命令使用默认账号及其所属平台
wkhelper course list
wkhelper auth status

# 删除指定账号凭据
wkhelper auth remove -p ykt -a 123456 --confirm
```

> [!NOTE]
> 凭据数据库与操作系统 Keyring 主密钥一一对应。备份或迁移环境时需同时保留 SQLite 数据库与系统保管库中的主密钥。`--credential-mode session` 全程仅驻留内存，退出即销毁。

### 2. 课程查询 (`course`)

```bash
# 查询当前账号在学课程（支持有界分页）
wkhelper course list -p ykt --limit 10

# 查看指定课程详情、考核权重与成绩看板
wkhelper course inspect -c 1966245:31318907
```

### 3. 视频学习与抽帧 (`video`)

```bash
# 查看待完成视频列表
wkhelper video list -c 1966245:31318907 --status pending

# 检查单视频观看进度与限制
wkhelper video inspect -c 1966245:31318907 -v 75369628

# 学习单个视频（预览参数，不发送心跳）
wkhelper video learn -c 1966245:31318907 -v 75369628 --jitter 0.2 --dry-run

# 执行单个视频学习（写操作需显式附加 --confirm）
wkhelper video learn -c 1966245:31318907 -v 75369628 --jitter 0.2 --confirm

# 基于计划文件批量执行学习任务并记录检查点
wkhelper video learn --plan-file plan.json --checkpoint-file cp.json --confirm

# 对视频进行抽帧截图（指定时间戳或均匀采样）
wkhelper video snapshot -c 1966245:31318907 -v 75369628 --timestamps 30,60,90
wkhelper video snapshot -c 1966245:31318907 -v 75369628 --count 5 -o ./frames
```

> [!TIP]
> 批量执行支持断点续学。传入 `--resume-from cp.json` 可跳过已达到阈值的视频；遇到停滞任务时将依 `--on-error` 策略决定终止或跳过。

### 4. 作业与题目检索 (`homework` / `question`)

```bash
# 查看未完成作业列表
wkhelper homework list -c 1966245:31318907 --status pending

# 查看作业元数据与题目题型大纲
wkhelper homework inspect -c 1966245:31318907 -w 84704898

# 查看单道题目的完整反混淆题干、选项与作答结果
wkhelper question get -c 1966245:31318907 -w 84704898 -q 73577720

# 提交整份作业（支持自定义间隔与随机顺序）
wkhelper homework submit -c 1966245:31318907 -w 84704898 \
  --answers '{"73577720": ["C"], "73577724": ["B"]}' \
  --concurrency 1 --delay 8.0 --delay-jitter 3.0 --random-order --confirm

# 导出全课程作业题目为 Markdown（可用于离线归档或模型分析）
wkhelper questions export -c 1966245:31318907 -o ./questions.md
```

### 5. 课件与资料 (`article`)

```bash
# 查询待阅读资料列表
wkhelper article list -c 1966245:31318907 --status pending

# 标记资料为已读完成
wkhelper article read -c 1966245:31318907 -a 84705076 --confirm
```

### 6. 讨论区互动 (`discussion`)

```bash
# 查询未完成的讨论题目
wkhelper discussion list -c 1966245:31318907 --status pending

# 查看讨论题详情与教师要求
wkhelper discussion inspect -c 1966245:31318907 -d 84704891

# 发表讨论回复完成平时分要求
wkhelper discussion reply -c 1966245:31318907 -d 84704891 \
  --content "今天的心得体会与学习总结..." --confirm

# 删除已发表的回复发言
wkhelper discussion delete -c 1966245:31318907 -m 8888 --confirm
```

### 7. 课程公告 (`notice`)

```bash
# 查询课程公告列表
wkhelper notice list -c 1966245:31318907 --status pending

# 查看公告详情与正文
wkhelper notice inspect -c 1966245:31318907 -n 84705560
```

### 8. 考试与考场管理 (`exam`)

```bash
# 查询课程考试列表与截止时间
wkhelper exam list -c 1966245:31318907

# 查看考试封面、须知、分值与监考状态
wkhelper exam inspect -c 1966245:31318907 -e 84705331

# 打开考场浏览器窗口（停留在考场入口）
wkhelper exam start -c 1966245:31318907 -e 84705331 --confirm

# 在浏览器中自动点击“开始答题”并启动计时
wkhelper exam start -c 1966245:31318907 -e 84705331 --begin --confirm

# 查看考场试卷题目（只读）
wkhelper exam paper -c 1966245:31318907 -e 84705331

# 导出考场试卷为 Markdown 或 JSON
wkhelper exam export -c 1966245:31318907 -e 84705331 -o ./exam.md

# 在已打开的考场浏览器中作答单道题目
wkhelper exam answer -c 1966245:31318907 -e 84705331 -q 9001 --answer A --confirm

# 检查考场接口与暂存状态诊断
wkhelper exam status -c 1966245:31318907 -e 84705331

# 关闭考场浏览器并清理会话
wkhelper exam close
```

> [!IMPORTANT]
> 考场作答依托真实前台浏览器完成，最终交卷属于不可逆高危操作，必须由考生本人在考场页面核对后自行点击。启用在线人脸监考、截屏检测或线下考场的考试会触发安全锁定，拒绝执行自动化写操作。

### 9. 命令规范与 Schema 自省 (`schema`)

```bash
# 查看所有命令列表与摘要索引
wkhelper schema

# 展开特定命令的完整参数契约与 Schema
wkhelper schema --path "homework submit" --full
```

---

## 防风控参数与会话配置

| 作用域 | 参数 | 默认值 | 说明 |
|---|---|---|---|
| **全局** | `--proxy <url>` | 无 | 全局网络流量代理（支持 HTTP / SOCKS5） |
| **全局** | `--user-agent <ua>` | 自动模拟 | 自定义浏览器 User-Agent（指定后回退为通用指纹） |
| **全局** | `--format <json\|md>` | 自动推断 | 统一输出格式，终端环境下默认 `md`，脚本管线默认 `json` |
| **全局** | `--credential-mode <system\|session>` | `system` | 凭据存储模式（`system` 为加密存储，`session` 为纯内存） |
| **全局** | `--data-dir <dir>` | 默认数据目录 | 凭据数据库与检查点存储目录 |
| **全局** | `--cookie-file <path>` | 无 | 静态会话 Cookie 文件（需配合 `--credential-mode session`） |
| **作业提交** | `--concurrency <N>` | `1` | 并发提交任务数，默认单任务顺序提交 |
| **作业提交** | `--delay <sec>` | `8.0` | 单题提交基础间隔秒数 |
| **作业提交** | `--delay-jitter <sec>`| `3.0` | 提交间隔随机抖动秒数，实际间隔为 `delay ± jitter` |
| **作业提交** | `--random-order` | `false` | 随机打乱题目提交顺序 |
| **单题/考试** | `--delay <sec>` | `0.0` | 提交前等待思考秒数 |
| **单题/考试** | `--delay-jitter <sec>`| `0.0` | 提交等待随机浮动秒数 |
| **视频学习** | `--jitter <ratio>` | `0.2` | 心跳间隔随机浮动比例（如 `1.5s ± 20%`） |
| **视频学习** | `--video-delay <sec>` | `3.0` | 连续视频切换之间的冷却等待秒数 |

---

## 调试模式与事件流 (`--debug`)

追加全局 `--debug` 选项可输出面向 Agent 的排错数据：

```bash
wkhelper --debug course inspect -c 1966245:31318907
```

- **异常诊断**：错误信封（stdout）自动携带结构化堆栈信息（`error.details.traceback`）。
- **实时网络事件**：stderr 通道输出 `http.trace` 等实时 NDJSON 事件，包含请求方法、URL、HTTP 状态码与耗时。

---

> [!WARNING]
> 本项目仅供接口逆向研究、自动化测试与辅助学习使用。请严格遵守各在线教育平台的用户协议及学校相关规章制度，切勿用于作弊或任何违规用途。
