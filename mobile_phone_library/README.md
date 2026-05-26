# 手机库

手机库负责统一管理发布手机、ADB 连接状态和基础健康检查。它不直接执行发布任务，而是给后续的任务队列提供可靠的手机状态。

## 已包含能力

- 检查项目内嵌的 `platform-tools/adb.exe` 是否可用。
- 读取当前 ADB 设备列表，并可把设备登记到手机库。
- 手动新增或更新手机信息，包括账号名、账号类型、连接方式、应用包名、手机相册目录。
- 支持无线调试配对入口：先配对，再连接，并写入手机库。
- 对手机做健康检查：ADB 是否在线、是否已授权、截图是否可用、目标应用是否存在、相册目录是否可写。
- 保存每次健康检查记录，方便排查哪台手机在什么时候不可用。

## 常用命令

```powershell
python -m mobile_phone_library init-db
python -m mobile_phone_library adb-status
python -m mobile_phone_library devices --register --account-type marketing --app-package com.zhiliaoapp.musically
python -m mobile_phone_library add --name "橱窗手机 01" --serial DEVICE_SERIAL --account "shop-account-01" --account-type showcase
python -m mobile_phone_library list
python -m mobile_phone_library health PHN-XXXXXXXXXXXX
python -m mobile_phone_library health-all
```

无线调试配对：

```powershell
python -m mobile_phone_library pair-wireless --host 192.168.1.23 --pair-port 37123 --pair-code 123456 --connect-port 39123 --name "发布手机 01"
```

如果手机已经配对过，只需要连接：

```powershell
python -m mobile_phone_library connect-wireless --host 192.168.1.23 --connect-port 39123
```

## 状态说明

- `online_idle`：手机在线、已授权，当前可分配任务。
- `running`：手机正在执行任务。
- `offline`：ADB 不在线或未连接。
- `error`：手机在线但基础检查失败，需要人工确认。
- `disabled`：人工停用，不参与任务分配。

## 账号类型

- `marketing`：营销号，按发布任务的时间配置走定时/预约发布工作流。
- `showcase`：橱窗号，发布前会额外执行商品挂载步骤，需要任务里带 `product_link` 或 `product_name`。
