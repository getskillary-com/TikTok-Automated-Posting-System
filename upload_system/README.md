# Upload System Step Pipeline

这个项目现在采用“独立步骤脚本 + 流水线执行器”的结构。每个操作都在 `workflow_steps/` 里有自己的脚本，方便后续单独调试和维护。

## 工作流

1. `push_video.py`：把指定本地视频分发到手机。
2. `open_target_app.py`：找到 TikTok Studio/TikTok 包名并打开。
3. `tap_upload.py`：OCR/UI 识别上传按钮并点击。
4. `select_first_video.py`：选择媒体选择器里的第一部视频。
5. `tap_next_after_select.py`：找到并点击第一次下一步。
6. `tap_next_after_edit.py`：找到并继续点击下一步。
7. `paste_caption.py`：按视频文件匹配预设文案，点击添加描述并粘贴。
8. `attach_product_link.py`：橱窗号专用，进入商品/链接入口，搜索或粘贴商品链接并选择商品。
9. `tap_publish.py`：找到并点击发布。
10. `minimize_and_cleanup.py`：确认上传/发布不再显示进度后，最小化应用并清理后台。

共享工具在 `workflow_steps/common.py`，流水线入口是 `workflow_steps/run_pipeline.py`。

## 准备

手机侧：

1. Android 开启开发者选项和 USB 调试。
2. 安装 TikTok Studio 或 TikTok，并提前登录。
3. 用 USB 连接电脑，手机上允许调试。

电脑侧：

1. 安装 Android Platform Tools，让 `adb.exe` 可用。
2. 安装本地 PaddleOCR。默认 OCR provider 是 `paddle_ocr`，不需要 Google API Key：

```powershell
python -m pip install paddlepaddle==2.6.2 -i https://www.paddlepaddle.org.cn/packages/stable/cpu/
python -m pip install paddleocr==2.8.1
```

如果仍要切回 Google Cloud Vision，把 `config.json` 里的 `recognition.provider` 改成 `google_ocr`，启用 Google Cloud Vision API，并设置 `GOOGLE_CLOUD_VISION_API_KEY`。

如果 `adb` 没进 PATH，在 `config.json` 里设置：

```json
{
  "adb": {
    "path": "C:\\Android\\platform-tools\\adb.exe"
  }
}
```

初始化配置：

```powershell
python -m upload_system init-config --output config.json
python -m upload_system doctor
```

如果检测到多个包名，把 TikTok Studio 的包名写进 `config.json` 的 `adb.app_package`。

## 文案预设

编辑：

```text
workflow_steps/caption_presets.json
```

支持三种匹配方式：

```json
{
  "default": "默认文案",
  "videos": {
    "demo.mp4": "demo.mp4 专用文案",
    "demo": "文件名 stem 为 demo 时使用"
  },
  "presets": {
    "product": "指定 --caption-preset product 时使用"
  }
}
```

优先级：

1. `--caption "直接文案"`
2. `--caption-preset product`
3. `videos` 中的完整文件名，例如 `demo.mp4`
4. `videos` 中的文件 stem，例如 `demo`
5. `default`

## 流水线执行

先 dry-run。dry-run 会截图、OCR、保存 trace，但不会点击或粘贴：

```powershell
python workflow_steps\run_pipeline.py .\videos\demo.mp4 --dry-run
```

真实执行，但最后发布会被拦截：

```powershell
python workflow_steps\run_pipeline.py .\videos\demo.mp4
```

允许最终发布：

```powershell
python workflow_steps\run_pipeline.py .\videos\demo.mp4 --allow-publish
```

指定文案预设：

```powershell
python workflow_steps\run_pipeline.py .\videos\demo.mp4 --caption-preset product --allow-publish
```

直接传文案：

```powershell
python workflow_steps\run_pipeline.py .\videos\demo.mp4 --caption "这里是本次视频文案" --allow-publish
```

## 单独运行某一步

流水线会生成状态文件，例如：

```text
runs/pipeline/20260428-120000/state.json
```

之后可以单独重跑某一步：

```powershell
python workflow_steps\tap_upload.py --state runs\pipeline\20260428-120000\state.json
python workflow_steps\paste_caption.py --state runs\pipeline\20260428-120000\state.json --caption-preset product
```

也可以从某一步恢复流水线：

```powershell
python workflow_steps\run_pipeline.py --state runs\pipeline\20260428-120000\state.json --from-step 5 --to-step 9 --allow-publish
```

## 调试产物

每次流水线都会保存：

```text
runs/pipeline/<run_id>/screenshots/*.png
runs/pipeline/<run_id>/xml/*.xml
runs/pipeline/<run_id>/traces/*.json
runs/pipeline/<run_id>/state.json
```

如果某一步点错，优先看对应的 `traces/*.json` 和 `screenshots/*.png`。

## 维护重点

每个步骤的按钮词表在 `config.json` 的 `pipeline.targets`：

```json
{
  "pipeline": {
    "targets": {
      "upload": ["Upload", "上传"],
      "next": ["Next", "下一步"],
      "caption_field": ["Add description", "添加描述"],
      "publish": ["Post", "Publish", "发布"]
    }
  }
}
```

第一部视频和描述框的兜底坐标在 `pipeline.fallbacks`，坐标是屏幕宽高比例：

```json
{
  "pipeline": {
    "fallbacks": {
      "first_video": {"x_ratio": 0.18, "y_ratio": 0.32},
      "caption_field": {"x_ratio": 0.5, "y_ratio": 0.22}
    }
  }
}
```

粘贴文案默认使用 Android 剪贴板加 `KEYCODE_PASTE`。如果你的手机 ROM 不支持 `cmd clipboard set text`，可以在 `config.json` 里调整 `pipeline.paste`，或者安装支持 ADB 输入中文的输入法方案后再扩展 `paste_caption.py`。

## 定时发布时间

发布流水线现在按账号类型分为两类：

- 营销号：默认工作流，重点是按 `publish_mode` 和 `scheduled_at` / `schedule-time` 控制发布时间。
- 橱窗号：在发布前额外执行商品挂载，并在 TikTok App 内设置预约发布时间；需要提供商品搜索词或商品名。

`publish_mode=scheduled` 使用 App/Studio 内置预约控件，不是让电脑等到时间再点发布。

巴西常用时区：

```text
America/Sao_Paulo
```

示例，巴西时间早上 8 点发布：

```powershell
python workflow_steps\run_pipeline.py ".\1.1.mp4" --caption "Good morning" --schedule-date 2026-04-29 --schedule-time 08:00 --schedule-timezone America/Sao_Paulo --allow-publish
```

如果当天巴西时间的 08:00 已经过了，脚本会自动等到下一天的 08:00。也可以指定日期：

```powershell
python workflow_steps\run_pipeline.py ".\1.1.mp4" --caption "Good morning" --schedule-date 2026-04-29 --schedule-time 08:00 --schedule-timezone America/Sao_Paulo --allow-publish
```

## TikTok Studio 内置预约发布

`--publish-mode scheduled` 使用 TikTok Studio 自带的预约发布功能，不是让电脑等到时间再点发布。预约发布必须同时传入 `--schedule-date` 和 `--schedule-time`，日期最多只能是巴西时间今天起未来 30 天内。

单轮示例：

```powershell
python workflow_steps\run_pipeline.py ".\1.1.mp4" --caption "Good morning" --schedule-date 2026-04-29 --schedule-time 08:00 --schedule-timezone America/Sao_Paulo --allow-publish
```

定时发布流水线：

```text
01_push_video
02_open_target_app
03_tap_upload
04_select_first_video
05_tap_next_after_select
06_tap_next_after_edit
07_paste_caption
open_schedule_settings
set_schedule_datetime
tap_scheduled_publish
minimize_and_cleanup
```

旧的“电脑等待到点再发布”保留为备选模式：

```powershell
python workflow_steps\run_pipeline.py ".\1.1.mp4" --caption "Good morning" --publish-mode timed --schedule-time 08:00 --schedule-timezone America/Sao_Paulo --allow-publish
```

## 橱窗号商品挂载

橱窗号执行时加 `--account-type showcase`，并提供商品链接或商品关键词：

```powershell
python workflow_steps\run_pipeline.py ".\1.1.mp4" --account-type showcase --publish-mode scheduled --scheduled-at "2026-04-29T08:00:00-03:00" --caption "Product caption" --product-link "https://shop.example.com/product/123" --product-name "Product keyword" --allow-publish
```

橱窗号流水线：

```text
push_video
open_target_app
tap_upload
refresh_media_picker
select_first_video
tap_next_after_select
tap_next_after_edit
paste_caption
attach_product_link
open_more_settings
open_schedule_publish
wait_schedule_picker_open
set_schedule_datetime
verify_schedule_configured
close_more_settings
tap_publish / tap_scheduled_publish
minimize_and_cleanup
```

商品挂载按钮词表在 `pipeline.targets.product_entry`、`product_search_field`、`product_select`、`product_confirm`，兜底坐标在 `pipeline.fallbacks.product_*`。不同 TikTok/TikTok Shop 版本入口位置有差异时，优先调整这些配置。

## JSON 发布任务

可以把视频、文案、发布日期、发布时间放进一个 JSON 文件，然后按任务 ID 执行。

示例文件：

```text
workflow_steps/post_jobs.json
```

格式：

```json
{
  "defaults": {
    "publish_mode": "scheduled",
    "account_type": "marketing",
    "schedule_timezone": "America/Sao_Paulo"
  },
  "jobs": {
    "test-1": {
      "video": ".\\1.1.mp4",
      "caption": "Good morning",
      "schedule": {
        "date": "2026-04-30",
        "time": "08:00"
      }
    },
    "showcase-1": {
      "account_type": "showcase",
      "publish_mode": "scheduled",
      "video": ".\\1.1.mp4",
      "caption": "Product caption",
      "scheduled_at": "2026-04-29T08:00:00-03:00",
      "product_link": "https://shop.example.com/product/123",
      "product_name": "Product keyword"
    }
  }
}
```

查看任务：

```powershell
python workflow_steps\run_pipeline.py --job-file workflow_steps\post_jobs.json --list-jobs
```

执行指定任务：

```powershell
python workflow_steps\run_pipeline.py --job-file workflow_steps\post_jobs.json --job-id test-1 --allow-publish
```

