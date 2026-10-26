[下载最新版 Windows 分享包](https://github.com/zzk14058108-commits/codex-quota-monitor/releases/latest) · [隐私与说明复核](AUDIT.md)

# Codex 额度自动暂停与原会话自动恢复（Windows分享版）

分享包版本：1.6.1-share.2。运行核心：1.6.1-desktop-auto-resume-plain-text。

这是独立的本地工具，不是OpenAI官方组件。请先完整解压，再运行；不要直接在压缩包预览中双击脚本。无需安装任何skill，也不需要DeepSeek。

## 它做什么

- 明确读到五小时额度或周额度剩余≤5%时，只请求中断已有桌面任务，保持Codex应用打开。
- 取得新的五小时窗口（300分钟）剩余>5%的有效读数，并确认周窗口（10080分钟）也>5%后，向原桌面会话写入一条可见的继续消息，从已有进度继续。
- 读取超时、缺失、过期或格式异常只记为“未知”；不能当成低额度，也不能代替恢复读数。周额度不能冒充五小时额度。
- 只恢复本工具实际暂停的工作；用户已经发新消息/手动继续、工作已完成或归档时不重放。发送结果不明不会反复重试。
- 不新建模型会话，不打开或关闭Codex应用，不影响独立CLI或其他设备的任务，也不会自动切换你正在查看的聊天。

额度检查只查询账户，不进行模型推理；真正恢复后的正常模型工作会按通常方式使用额度。检查存在轮询和通信延迟，并不保证账号读数绝不会短暂越过阈值。

## 安装前准备

1. 使用Windows 64位系统，并有可用的Windows PowerShell和任务计划程序。
2. 安装64位Python 3.12或更新版本，包含python.exe与pythonw.exe。该包只用Python标准库，不用pip安装依赖；不会自动下载软件。
3. 安装并登录Codex桌面端，至少打开过一个聊天。默认使用当前Windows用户的账户与本地Codex目录；不需要把任何账户文件复制到此包。
4. 已有本工具或其它同名额度监控时，先用原版本的卸载入口移除其计划任务，再安装。仅停止或禁用旧任务仍会被检测为“已有安装”；新安装器会拒绝覆盖它。

Python 可从 [Windows 官方下载页](https://www.python.org/downloads/windows/) 获取。安装时保留 Python 启动器，并确认同时有 python.exe 和 pythonw.exe；本工具不要求安装第三方Python包。

原运行核心已在Codex桌面版本26.1021.3705.0完成12项实测检查、66项隔离检查，包含4%明确注入、原会话恢复和不关闭应用。其它电脑/版本尚未逐一实测，先运行自检。分享包不包含4%注入启动器，不会自动打断聊天来“测试”。

## 最简单的使用方法

1. 解压到普通文件夹。
2. 双击 `Install.cmd`。默认安装到 `%LOCALAPPDATA%\CodexQuotaMonitor`，创建当前用户登录自启任务 `CodexQuotaMonitorPortable`，随后启动监控。
3. 保持Codex打开，双击 `Doctor.cmd` 做一次只读兼容性检查。它会优先读取默认安装目录的配置，只查询额度与桌面会话设置，不暂停、不恢复、不调用模型。
4. 日常无需打开脚本窗口；到阈值会暂停，明确恢复后会在原聊天出现带 `quota-resume` 标记的继续消息。

可运行 `Status.cmd` 查看状态；`Disable.cmd` 停止监控并关闭登录自启；`Enable.cmd` 重新启用。

如果找不到Python，在PowerShell中提供它的完整路径：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\Install.ps1 -PythonExe 'C:\Python312\python.exe'
```

如果找不到Codex命令行程序，提供实际存在的codex.exe路径（不要填ChatGPT.exe）。文件位置因安装方式而异：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\Install.ps1 -CodexExe 'D:\Apps\Codex\bin\codex.exe'
```

上面路径只是示例。常见目录是 `%LOCALAPPDATA%\OpenAI\Codex\bin` 和 `%LOCALAPPDATA%\Programs\OpenAI\Codex\bin`；必须选择其中实际存在的codex.exe，不能仅按目录名称或“最新目录”猜测。手动指定的codex.exe路径失效后，可修改安装目录的 `runtime.json`，或先卸载后重新安装；默认自动发现方式会重新查找可用目录。

也可显式传入 `-CodexHome` 或 `-InstallDir`。安装器不覆盖非空目标目录。遇到任务计划程序权限错误时，安装未完成；先解决当前用户的任务注册权限，保留诊断资料，再使用新的空目录安装。不要通过关闭安全软件或修改Codex文件来绕过错误。

`-ExecutionPolicy Bypass`只作用于本次脚本进程，不修改系统或用户的永久执行策略。批处理入口使用相同方式启动可信的本包脚本。

## 管理、卸载和关闭自动恢复

解压目录里的管理入口也可以操作默认安装目录：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\Manage.ps1 -Action Status
powershell -NoProfile -ExecutionPolicy Bypass -File .\Manage.ps1 -Action Disable
powershell -NoProfile -ExecutionPolicy Bypass -File .\Manage.ps1 -Action Enable
powershell -NoProfile -ExecutionPolicy Bypass -File .\Uninstall.ps1
```

自定义安装目录时，Manage、Uninstall和Doctor都应追加 `-InstallDir '实际路径'`。也可进入实际安装目录运行相应的ps1。`Uninstall.cmd`用于默认安装目录。

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\Doctor.ps1 -InstallDir 'D:\Tools\CodexQuotaMonitor'
```

此路径同样只是示例。自检会读取该安装自己的Python、codex.exe和Codex用户目录配置。

卸载只停止该工具并移除其计划任务，不关闭Codex，不删除聊天。安装文件和本机日志保留在安装目录，需要时自行删除。

只想关闭自动恢复：把安装目录 `config.json` 中的 `auto_resume_monitor_paused_tasks` 改为 `false`，然后用Manage的Stop等待停止，再Enable重启监控。暂停功能仍保留，之后由用户手动继续。

## 自检与更新

`Doctor.cmd`通过只读方式检查Python、已验证核心哈希、codex.exe、桌面会话目录、五小时/周额度结构及原生桌面所有者。局部检查可运行：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\Install.ps1 -CheckOnly
powershell -NoProfile -ExecutionPolicy Bypass -File .\Doctor.ps1 -LocalOnly
```

自检失败不能证明额度不足。可以查看安装目录的 `status.json` 和 `events.jsonl`；不要把含聊天标识或本机路径的运行日志未经检查就公开分享。

### 状态与常见问题

| 状态/现象 | 含义与处理 |
|---|---|
| `ALLOWED` | 当前有效读数允许工作；正常情况下无需操作。 |
| `BLOCKED_LOW_QUOTA` | 明确读到低额度；保持Codex打开，等待新的五小时与周读数都高于5%。 |
| `QUOTA_UNKNOWN` | 读取失败、数据不完整或过期。它不是“额度低”；检查登录、网络和Codex版本，不要改成低额度来兜底。 |
| 找不到codex.exe | 提供实际文件路径；不要使用ChatGPT.exe。自检可直接传`-CodexExe`。 |
| 找不到桌面会话目录/结构不符 | 确认已登录桌面端并打开过聊天；自定义Codex目录传`-CodexHome`。版本结构改变时需要适配。 |
| 找不到桌面所有者 | 保持Codex及一个已有聊天打开；默认只尝试最近3个未归档聊天。需要时给Doctor传`-ThreadId`，不要公开该ID。 |
| 发送结果不明或恢复前检查多次失败 | 工具不会重复派发。检查`status.json`，额度明确恢复后可在原聊天手动继续。 |
| 安装器提示已有监控 | 用旧安装自己的卸载入口处理；不要删除不认识的任务或Codex进程。 |

升级本分享包前，先通过旧版Uninstall移除监控任务。安装器不会覆盖非空目录：保留旧目录及日志，在新的空目录安装；若要复用原路径，先备份旧目录再由用户自行清理。不要把旧的暂停队列、测试授权或恢复回执复制进新安装，以免重放历史工作。

### 核对下载文件

从同一GitHub Release同时下载ZIP和`.sha256`文件，在下载目录运行：

```powershell
Get-FileHash -LiteralPath '.\CodexQuotaMonitor-1.6.1-share.2.zip' -Algorithm SHA256
Get-Content -LiteralPath '.\CodexQuotaMonitor-1.6.1-share.2.zip.sha256'
```

两处哈希值应一致（不区分大小写）。压缩包内的MANIFEST用于逐文件检查；它不能证明发行者身份，仍需确认下载自正确仓库。

Codex更新后先重新做只读自检。桌面IPC或本地目录结构变更可能需要适配；只读检查通过也不能代替新版本的完整暂停/恢复验收。本包不会自动改写自身代码，不会为了“兼容”转去后台启动模型或强杀应用。

## 文件与隐私

- `core/`：与原固定版本逐字节一致的运行核心。
- 安装/管理/卸载入口：按接收者自己的用户目录与Python位置配置。
- `MANIFEST.json`：文件SHA256，可检测解压损坏；不等同于发行者数字签名。
- `tests/`：隔离检查，不进行真实模型调用；`Run-Tests.cmd`可运行。
- `验证说明.md`：原核心验证范围及本次打包验证边界。

压缩包不含发送者的账户凭据、API密钥、会话ID、SID、个人运行日志、暂停队列或测试授权。`runtime.json`和`install.json`由接收者安装时生成，包含接收者自己的本机路径，不应混入再次转发的原始分享包。
