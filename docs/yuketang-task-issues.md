# 雨课堂章节作业、讨论状态与考场会话问题

实测环境：macOS，Python 3.13，WkHelper 0.1.0；安装提交 `ca8f9308382e62fa37f4b0a140b162c5654ed33d`。

另对照主分支提交 `304c0a94da5c428604784e7e3df99438d0893a57`（版本 1.0.0）源码，本文指出的三个判断路径仍存在；未在该版本重新执行平台写操作。

## 1. 雨课堂章节作业被误判为考试，阻断题目读取、导出与提交

### 复现

```sh
wkhelper homework inspect --course 2458997:33509273 --homework 90062280
wkhelper questions export --course 2458997:33509273 --output questions.md
```

第一条返回 `PLATFORM_ERROR`，提示节点 `leaf_type=6` 为考试，要求改用 `exam inspect`。第二条因六份作业均无法抓取而返回 `INTERNAL_ERROR`，并输出内部堆栈。

### 预期与实际依据

浏览器将这些节点展示为“作业”，通过 `/v2/web/iframe-exercise/33509273/90062292` 等页面提供逐题作答。六份作业分别有 12、19、20、20、20、16 题，共 107 题。实测原有习题接口能够读取并反混淆全部题目，原有作业提交接口也能成功提交；最终六份作业的 `completed` 均为 `true`。

### 原因与建议

`core/base_platform.py::get_leaf_questions` 在读取习题内容前，直接将 `(5, 6, 7, 9)` 全部判定为考试。该映射不适用于本次雨课堂的章节作业。

应修正雨课堂节点分类，让这种受支持的章节作业进入原有习题路径，同时保留真正考试的处理路径。导出遇到明确的平台错误时，应报告对应节点和错误原因，避免将整批抓取失败转成笼统内部异常。

## 2. discussion list 与 discussion inspect 对同一讨论返回不同完成状态

### 复现

在提交讨论发言后查询：

```sh
wkhelper discussion list --course 2458997:33509273
wkhelper discussion inspect --course 2458997:33509273 --discussion 90062281
```

本次七个讨论均提交成功且获得评论 ID。列表仍对七项返回 `completed=false`，逐项详情均返回 `completed=true`；浏览器显示“已发言”。详情中的 `user_comment_num` 仍为 0。

### 原因与建议

`BasePlatform.get_discussions` 仅按 `_get_leaf_schedules` 的进度是否达到 1 判断完成；雨课堂详情通过 `_discussion_completed`，在 `user_comment_num=0` 时另查 `/v/discussion/v2/student/comment/status/`。

两个命令使用不同的状态来源。列表应在必要时采用与详情相同的已发言判断，避免把已完成讨论列为待完成，导致工作流重复发言。

## 3. 考场命令在空令牌及不可用会话下仍报告成功

### 复现

```sh
wkhelper exam inspect --course 2458997:33509273 --exam 90062280
wkhelper exam start --course 2458997:33509273 --exam 90062280 --confirm
wkhelper exam paper --course 2458997:33509273 --exam 90062280
```

`inspect` 返回题数 0、满分 0、限时 0、状态“未知”。`start` 返回 `ok=true`、`started=true`，消息声称已启动服务端计时并完成令牌授权，但返回地址中的 `crypt` 为空。随后 `paper` 访问考场接口返回 HTTP 401。课程查询、讨论与普通习题接口仍可使用。

### 原因与建议

`platform/yuketang.py::_mint_exam_room` 在令牌缺失时使用空字符串，继续访问登录地址；`start_exam` 随后直接报告成功。这里实际输入来自问题 1 中被误分类的作业，因此现有证据不能证明真正考试也存在认证故障。

应在空令牌时返回清楚的平台错误，不报告授权或会话建立成功。未调用开始作答接口时，提示文案也应准确区分“建立会话”和“开始计时”。

