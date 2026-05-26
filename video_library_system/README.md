# 视频库第一阶段

视频库负责管理电脑本地视频资产，不直接控制手机发布。

第一阶段已经包含：

- 初始化 SQLite 数据库
- 导入单个视频
- 扫描文件夹批量导入视频
- 记录文件路径、文件名、大小、格式、SHA256
- 可选读取时长和分辨率
- 按文件指纹去重
- 维护视频状态
- 查询视频列表和详情

常用命令：

```powershell
python -m video_library_system init-db
python -m video_library_system import-file ".\upload_system\1.1.mp4" --batch "test" --tags "demo,upload"
python -m video_library_system scan-folder "D:\素材库" --batch "batch-001" --tags "product"
python -m video_library_system list --status unused
python -m video_library_system show VID-XXXXXXXXXXXX
```

默认数据库路径：

```text
storage/group_control_system.db
```
