# 执行队列

执行队列负责把发布任务交给 `upload_system` 执行。它会先锁定任务和手机，生成本次运行目录、临时上传配置、状态文件和命令记录，然后根据上传流水线结果回写任务状态。

## 核心流程

1. 从发布任务库读取可执行任务。
2. 检查任务是否进入执行窗口。
3. 检查手机是否可用。
4. 把任务和手机标记为 `running`。
5. 生成本次运行目录和专属 `upload_config.json`。
6. 调用 `upload_system/workflow_steps/run_pipeline.py`，并按手机账号类型选择营销号或橱窗号工作流。
7. 成功后标记任务为 `published`，并更新视频、文案、手机计数。
8. 失败后写入失败原因，并根据重试次数决定回到 `pending` 还是变成 `failed`。

## 常用命令

预览可执行任务：

```powershell
python -m execution_queue preview
```

只生成运行文件和命令，不调用手机：

```powershell
python -m execution_queue run-once --dry-run --ignore-phone-status
```

真实执行发布，必须显式允许最终发布：

```powershell
python -m execution_queue run-once --allow-publish
```

持续轮询并执行可领取任务：

```powershell
python -m execution_queue run-loop --allow-publish --poll-seconds 5
```

如果只想跑完当前可执行任务后退出，可以加：

```powershell
python -m execution_queue run-loop --allow-publish --max-runs 1 --stop-when-idle
```

如果只是想让上传流水线进入它自己的 dry-run，不点击但仍会读取手机屏幕：

```powershell
python -m execution_queue run-once --allow-publish --pipeline-dry-run
```

## 多手机并行执行

正式并行执行使用 `workers`。它会为每台可用手机启动一个 `PhoneWorker`：单台手机内部串行领取任务，多台手机之间并行执行。

```powershell
python -m execution_queue workers --allow-publish --max-workers 5 --stagger-seconds 25
```

营销号常驻 worker 可以只选择当前账号类型为 `marketing` 的手机，空闲时不退出，默认每 `5` 秒轮询一次新任务：

```powershell
python -m execution_queue workers --allow-publish --account-type marketing --max-workers 4 --expected-worker-count 4 --stagger-seconds 25
```

`--account-type` 只用于启动时筛选手机。每个长期运行的 `PhoneWorker` 不缓存账号类型；它领取任务时会重新读取数据库里的 `phones.account_type`，所以手机从营销号改成橱窗号后，下一次领取会按橱窗号 workflow 生成命令。

同一台手机在数据库层也有硬保护：只要该手机存在 `running` 任务，新的任务不会被领取；如果另一个活跃 `PhoneWorker` 已经绑定同一台手机，新的 worker 注册会被拒绝。单机串行任务之间默认等待 `90` 秒，可通过 `--post-run-cooldown-seconds` 调整，避免上一条发布后的 TikTok/媒体库状态影响下一条。

Worker 默认会在手机空闲且没有当前任务时每 `300` 秒唤醒/解锁一次，避免长时间无通信后息屏。当前按所有手机无锁屏密码处理；如果以后小米/Redmi/POCO 重新设置 PIN，可通过环境变量 `GROUP_CONTROL_XIAOMI_UNLOCK_PIN` 临时启用 PIN 解锁。可用 `--idle-wake-interval-seconds` 调整间隔，或用 `--disable-idle-wake` 关闭。

`workers` 默认启用 5 台稳态准入门禁：启动前会恢复过期 worker、确认选中手机数为 `5`、ADB 在线设备不少于 `5`、手机库状态都是 `online_idle`、每台都有账号类型/应用包名/手机目录、profile 与型号/屏幕尺寸匹配，并且真实发布时 profile 不能是 `draft_requires_calibration`。调试单机时可显式加 `--disable-steady-state-gate`。

橱窗号 A52 现在使用托管相册模式：上传前会清理该手机发布目录里的视频，再推送当前任务视频；进入 TikTok 媒体选择页后会校验视频缩略图数量必须为 `1`，否则直接失败，避免固定坐标误选旧视频。

单机串行默认在任一任务失败后停止，不继续领取下一条，避免坏页面状态污染后续任务。确实需要失败后继续时，可显式加 `--continue-on-failure`。

常用调试方式：

```powershell
python -m execution_queue workers --allow-publish --pipeline-dry-run --max-workers 2 --stop-when-idle
```

只跑指定手机：

```powershell
python -m execution_queue workers --allow-publish --adb-serial SERIAL-EXAMPLE --stop-when-idle --disable-steady-state-gate
```

查看 worker 心跳：

```powershell
python -m execution_queue workers-status
```

轻量 supervisor 可以作为 1 天版本的常驻守护进程使用。它会恢复过期心跳、恢复已退出但还占着 lease 的 worker、顺延已过预约时间但还没执行的任务，并为缺失常驻 worker 的在线手机补齐 worker：

```powershell
python execution_queue_launcher.py supervisor --allow-publish --account-type all --timezone America/Sao_Paulo
```

先预览不改状态：

```powershell
python execution_queue_launcher.py supervisor --dry-run --once --account-type all --timezone America/Sao_Paulo
```

巴西14橱窗号使用专用 watchdog 常驻入口，避免后台 worker 领取任务后进程死亡但任务仍显示 `running`。watchdog 会按轮次启动单任务 worker，worker 退出后只恢复巴西14这台手机的 dead/stale worker 锁，再继续等待下一条任务：

```powershell
run_log\start-brazil14-showcase-watchdog.vbs
```

需要注册成 Windows 登录后自动启动时运行：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File run_log\install-brazil14-showcase-watchdog-task.ps1
```

主要日志：

```powershell
run_log\worker_processes\brazil14-showcase-watchdog.log
run_log\worker_processes\brazil14-showcase-worker.out.log
run_log\worker_processes\brazil14-showcase-worker.err.log
```

如果电脑重启或执行器异常退出，确认 worker 心跳已过期后可以手动释放卡住的任务：

```powershell
python -m execution_queue recover-stale
```

队列命令也支持按手机过滤：

```powershell
python -m execution_queue preview --phone-id PHN-xxx
python -m execution_queue run-once --allow-publish --adb-serial SERIAL-EXAMPLE
```

调试橱窗号时使用发布前硬停止，不需要 `--allow-publish`：

```powershell
python -m execution_queue run-once --stop-before-final-publish --phone-id PHN-xxx
python -m execution_queue workers --stop-before-final-publish --account-type showcase --max-workers 1
```

该模式会真实走到商品挂载、商品名填写、预约设置和预约验证，但在 `tap_scheduled_publish` / `tap_publish` 前停止，并将任务标记为 `debug_ready`。

## 关于预约任务的执行窗口

`scheduled` 任务的 `scheduled_at` 表示最终发布时间，不是开始执行时间。营销号任务上传入库后会立即进入可领取状态，由 TikTok Studio 负责预约到 `scheduled_at`；worker 空闲时默认每 `5` 秒检查一次新任务。

`--preparation-window-minutes` 仍保留给非营销号的定时类调试入口：

```powershell
python -m execution_queue preview --preparation-window-minutes 90
```

如果目标发布时间已经过去，默认不会执行，因为 TikTok Studio 原生预约通常要求未来时间。只有明确传入以下参数才会放行：

```powershell
python -m execution_queue run-once --allow-publish --allow-overdue
```

supervisor 默认不强行执行过期预约任务，也不会 `--allow-overdue` 强发。它会把 `pending` / `ready` 且 `publish_mode=scheduled` / `timed` 的过期任务自动顺延到当前时间后 `30` 分钟，并保持 `pending` 重新入队，同时写入原因。需要只报告或暂停时可使用：

```powershell
python execution_queue_launcher.py supervisor --overdue-action report
python execution_queue_launcher.py supervisor --overdue-action pause
```

## 账号工作流

- 营销号手机（`account_type=marketing`）：保持原有定时/预约发布逻辑。
- 橱窗号手机（`account_type=showcase`）：`publish_mode=scheduled` 的任务会像营销号一样被 worker 尽快领取；上传流水线会先挂载商品，再在 TikTok App 内设置预约发布时间，最后点击发布。旧的 `publish_mode=immediate` 任务仍保留兼容路径。
- 橱窗号商品挂载不再使用商品搜索框；当前橱窗和 TikTok Shop 补货页都直接滑动扫描当前列表，通过 XML/OCR 识别商品标题前缀并点击对应 `添加/Add` 按钮。

## 安全开关

- 没有 `--allow-publish` 时，真实执行会被拒绝。
- `--stop-before-final-publish` 是例外：允许无 `--allow-publish` 运行到最终发布按钮前，并以 `debug_ready` 结束。
- `--dry-run` 不调用上传流水线，只生成计划并释放任务。
- `--pipeline-dry-run` 会调用上传流水线，但上传流水线仍会访问 ADB 和手机屏幕。
- 默认要求手机状态是 `online_idle`，测试命令可用 `--ignore-phone-status` 放宽。

## 终止全部任务

标准终止命令会备份数据库、停止 supervisor/worker 进程、清理 worker 状态，并默认把 `pending` / `ready` / `running` / `paused` 任务置为 `removed`：

```powershell
python -m execution_queue stop-all
```

只停 worker、保留任务时使用：

```powershell
python -m execution_queue stop-all --keep-tasks
```
