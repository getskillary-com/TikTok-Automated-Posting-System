# 控制中心接口层

控制中心把视频库、文案库、手机库、发布任务库、执行队列和运行日志统一成一个本地 JSON API，给后续 UI 页面调用。

## 启动

```powershell
python -m control_center serve --host 127.0.0.1 --port 8766
```

服务地址：

```text
http://127.0.0.1:8766
```

## 常用检查

```powershell
python -m control_center health
python -m control_center routes
python -m control_center routes --json
```

## 主要接口

- `GET /api/health`：系统健康检查
- `GET /api/summary`：模块数据数量汇总
- `GET /api/videos`：视频列表
- `POST /api/videos/import`：导入视频
- `GET /api/captions`：文案列表
- `POST /api/captions`：新增文案
- `POST /api/captions/bind-video`：绑定文案和视频
- `GET /api/phones`：手机列表
- `POST /api/phones`：新增手机，可传 `account_type=marketing|showcase`
- `GET /api/adb/status`：ADB 工具状态
- `GET /api/tasks`：发布任务列表
- `POST /api/tasks`：创建发布任务，橱窗号任务可传 `product_link` / `product_name`
- `POST /api/tasks/{task_id}/reschedule`：修改发布时间
- `POST /api/tasks/{task_id}/cancel`：取消任务
- `POST /api/tasks/{task_id}/requeue`：把失败、取消、暂缓任务重新放回待执行
- `GET /api/queue/preview`：执行队列预览
- `POST /api/queue/run-once`：执行一次任务
- `POST /api/queue/workers/start`：启动队列 worker；每台手机串行，多台手机并行
- `GET /api/queue/workers/status`：查看 worker 心跳、当前任务和最后错误
- `POST /api/queue/workers/stop`：停止当前控制中心启动的 worker
- `POST /api/queue/workers/recover-stale`：恢复过期 worker 锁定的任务
- `GET /api/logs`：运行日志列表
- `GET /api/logs/failures`：失败日志列表
- `GET /api/logs/{log_id}`：日志详情

## 安全说明

`POST /api/queue/run-once` 默认不会绕过执行队列的安全限制。真实发布必须在请求体里显式传入：

```json
{
  "allow_publish": true
}
```

调试到最终发布按钮前停止时使用：

```json
{
  "stop_before_final_publish": true
}
```

只生成运行计划、不连接手机时使用：

```json
{
  "dry_run": true,
  "ignore_phone_status": true
}
```

启动多手机并行 worker 时使用：

调试模式：

```json
{
  "stop_before_final_publish": true,
  "max_workers": 5,
  "max_runs_per_worker": 0,
  "stop_when_idle": false,
  "post_run_cooldown_seconds": 90,
  "idle_wake_enabled": true,
  "idle_wake_interval_seconds": 300,
  "steady_state_gate_enabled": true,
  "expected_worker_count": 5
}
```

正式发布模式：

```json
{
  "allow_publish": true,
  "max_workers": 5,
  "max_runs_per_worker": 0,
  "stop_when_idle": false,
  "post_run_cooldown_seconds": 90,
  "idle_wake_enabled": true,
  "idle_wake_interval_seconds": 300,
  "steady_state_gate_enabled": true,
  "expected_worker_count": 5
}
```

其中每台手机内部会串行领取任务；多台手机会并行运行。`steady_state_gate_enabled` 开启后，控制中心会在启动前检查 5 台准入门禁；未通过时返回 `blocked` 和具体问题，不会启动 worker。`idle_wake_enabled` 开启后，空闲且没有当前任务的手机会每 300 秒唤醒/解锁一次。`allow_publish` 和 `stop_before_final_publish` 不能同时开启；未选择真实发布或发布前停止时，UI 应默认按 `dry_run` 发起。

## 响应格式

成功：

```json
{
  "ok": true,
  "data": {}
}
```

失败：

```json
{
  "ok": false,
  "error": "错误原因"
}
```
