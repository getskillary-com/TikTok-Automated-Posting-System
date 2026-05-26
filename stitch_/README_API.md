# Stitch UI API 接入说明

这些页面是 Stitch 导出的静态 HTML。现在每个页面都会加载 `stitch_/shared/api-client.js`、`stitch_/shared/layout-normalizer.js`、`stitch_/shared/ui-bridge.js`、`stitch_/shared/video-library-app.js` 和 `stitch_/shared/sample-data-cleaner.js`，用于统一页面框架、单页模块切换，并读取本地控制中心 API。

## 启动控制中心 API

```powershell
cd "C:\Local Disk\Work\group_control_system"
python -m control_center serve --host 127.0.0.1 --port 8766
```

默认接口地址：

```text
http://127.0.0.1:8766/api
```

## 打开页面

推荐直接打开：

```text
stitch_\index.html
```

如果浏览器拦截本地文件请求，也可以把 `stitch_` 目录交给任意静态服务打开，但控制中心 API 仍然需要单独运行。

## 修改 API 地址

在浏览器控制台执行：

```javascript
localStorage.setItem("controlCenterApiBase", "http://127.0.0.1:8766/api")
```

恢复默认地址：

```javascript
localStorage.removeItem("controlCenterApiBase")
```

## 单页工作台

`stitch_/index.html` 是新的推荐入口。左侧菜单现在使用 `#/overview`、`#/video_library` 这类 hash 路由切换模块，点击模块只会重绘中间内容区，不再跳转到另一个 `code.html`，因此页面不会整页刷新。

为了兼容旧入口，8 个原来的 `*/code.html` 页面也加载同一套单页脚本。即使从旧页面打开，点击左侧模块也会在当前页面内切换。

## 全量 UI 接入

当前 UI 已经接入控制中心已暴露的主要管理、执行和排障动作：

- 视频库：查看详情、上传视频、按路径导入、扫描文件夹批量导入、修改状态，并可跳转生成任务。
- 文案库：新增、批量导入 TXT/JSON/CSV、编辑、改状态、绑定视频和查看绑定关系。
- 手机库：ADB 状态、USB 检测设备、按设备分别登记账号、手动添加、编辑、停用/启用、移除、单机/全量健康检查和检查历史。
- 发布任务：新建、从绑定关系创建、CSV 批量导入、改时间、改手机、改状态、取消和查看详情。
- 执行队列：预览、dry-run、pipeline dry-run、调试锁定任务，以及带双重确认的真实发布。
- 运行日志：日志详情、最新日志、任务报告、attempts、artifacts、stdout/stderr tail、截图/XML/trace 查看。
- 系统设置：上传系统 doctor/push/run、可视化编辑 `config.json` 和 `caption_presets.json`，保存前自动备份。

注意：新建发布任务前，需要至少有 1 条视频、1 条文案和 1 台手机。当前正式数据库里手机数量为 0，所以需要先在手机库登记或手动添加手机。

## 安全边界

前端默认不会触发真实发布。真实发布需要勾选“允许真实发布”；后端会校验 `allow_publish: true`。

## 统一布局

`layout-normalizer.js` 会隐藏 Stitch 每个页面自带的不一致侧边栏和顶部栏，并统一注入一套公共外壳：

- 左侧主导航
- 顶部标题栏
- ADB 运行状态
- 页面内容区边距和背景
- 移动端窄屏导航收缩

如果浏览器仍显示旧布局，请按 `Ctrl + F5` 强制刷新，确认页面底部脚本已重新加载。

## 视频文件选择上传

视频库的“导入视频”弹窗现在优先支持系统文件选择框。选择文件后，浏览器会把视频上传到控制中心 API，后端保存到 `storage/uploaded_videos`，再用保存后的真实路径登记到视频库。

如果不选择文件，也可以继续粘贴运行控制中心这台电脑上的完整视频路径，走原来的路径导入方式。

## 单页动态化

真实业务界面现在集中在 `shared/ui-bridge.js`，`shared/video-library-app.js` 仅保留兼容占位，避免视频库双渲染。进入各模块时会自动加载：

- 视频库主体表格直接读取 `GET /api/videos`
- “导入视频”表单默认只显示视频文件选择；标题、批次、标签、备注和按路径导入收在“高级选项”里
- “扫描文件夹”可先调用 `POST /api/videos/choose-folder` 让控制中心在本机弹出目录选择框，再调用 `POST /api/videos/scan-folder`
- “查看”会打开真实视频详情抽屉
- “生成任务”会跳转到发布任务页面并携带 `video_id`
- 其他模块使用同一个动态渲染器接入文案、手机、任务、队列、日志和设置 API

注意：浏览器文件选择器不会暴露真实本地路径，所以文件选择模式会直接上传文件，由后端保存后再登记真实路径。

## 样本数据清理

8 个 `code.html` 页面已经清空原始 Stitch 演示主体，只保留 `<main></main>` 作为真实数据渲染容器。

`sample-data-cleaner.js` 会在页面运行时继续移除 Stitch 原型里可能残留或异步插入的演示数据，例如演示任务、演示手机、演示日志和固定统计数字。它只清理前端 HTML 的样本展示，不会删除 `storage/group_control_system.db` 里的真实视频、文案、手机或任务记录。

清理后，页面会显示控制中心 API 返回的真实数据。
