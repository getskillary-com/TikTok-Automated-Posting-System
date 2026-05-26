# 文案库第一阶段

文案库负责管理发布文案，不直接执行发布任务。

第一阶段包含：

- 新增单条文案
- 从 TXT / JSON / CSV 批量导入文案
- 按文案内容去重
- 管理文案状态：unused / assigned / used / disabled
- 管理标签、平台、账号范围和备注
- 查询文案列表和详情
- 编辑文案内容和元数据
- 将文案绑定到视频

常用命令：

```powershell
python -m copywriting_library init-db
python -m copywriting_library add "春季上新大促，全场折起，不容错过。" --tags "促销,巴西" --platform "TikTok Studio"
python -m copywriting_library import-file ".\captions.txt" --tags "批量导入"
python -m copywriting_library list --status unused
python -m copywriting_library bind-video CPY-XXXXXXXXXXXX VID-XXXXXXXXXXXX
```

TXT 导入规则：用空行分隔多条文案。
CSV 导入规则：必须包含 `content` 字段，可选 `tags, platform, account_scope, note`。
JSON 导入规则：支持字符串数组、对象数组，或 `{ "captions": [...] }`。
