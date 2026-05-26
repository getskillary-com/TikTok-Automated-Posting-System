# 发布任务库

发布任务库负责把“视频、文案、发布时间、发布手机”整理成一条标准发布任务。它只管理任务数据，不连接手机，也不执行上传。

## 已包含能力

- 创建单条发布任务，并可为橱窗号任务保存商品链接/商品关键词。
- 从“文案和视频绑定关系”创建任务。
- 从 CSV 批量导入任务计划。
- 查询任务列表、查看任务详情、筛选待执行任务。
- 修改任务状态、改发布时间、换发布手机、取消任务。
- 创建任务时会检查视频、文案、手机是否存在，并默认防止同一个视频重复进入未完成任务。

## 常用命令

```powershell
python -m release_task_list init-db
python -m release_task_list list
python -m release_task_list create --video-id VID-XXXXXXXXXXXX --caption-id CPY-XXXXXXXXXXXX --phone-id PHN-XXXXXXXXXXXX --scheduled-at "2026-05-01 10:30"
python -m release_task_list create --video-id VID-XXXXXXXXXXXX --caption-id CPY-XXXXXXXXXXXX --phone-id PHN-SHOWCASE --scheduled-at "now" --publish-mode immediate --product-link "https://shop.example.com/product/123" --product-name "Product keyword"
python -m release_task_list next
python -m release_task_list show TSK-XXXXXXXXXXXX
python -m release_task_list reschedule TSK-XXXXXXXXXXXX "2026-05-01 14:00"
python -m release_task_list requeue TSK-XXXXXXXXXXXX --reset-retry-count
python -m release_task_list cancel TSK-XXXXXXXXXXXX --reason "手动取消"
```

`next` 使用和执行队列一致的筛选规则，会考虑预约任务准备窗口、手机状态、重试次数和过期任务策略。

## CSV 格式

CSV 至少需要包含：

```csv
video_id,caption_id,phone_id,scheduled_at,publish_mode,product_link,product_name,max_retries,note
VID-xxx,CPY-xxx,PHN-xxx,2026-05-01 10:30,scheduled,,,3,第一条任务
VID-shop,CPY-shop,PHN-shop,now,immediate,https://shop.example.com/product/123,Product keyword,3,橱窗号商品挂载任务
```

`caption_id` 可以为空，`publish_mode` 默认 `scheduled`，`max_retries` 默认 `3`。橱窗号任务至少填写 `product_link` 或 `product_name` 之一。

## 状态说明

- `pending`：已创建，等待执行。
- `ready`：已确认可执行。
- `running`：正在执行。
- `published`：发布完成。
- `failed`：发布失败。
- `cancelled`：已取消。
- `paused`：暂缓执行。
