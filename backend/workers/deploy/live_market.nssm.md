# Windows 服务化（NSSM）安装步骤

[NSSM (Non-Sucking Service Manager)](https://nssm.cc/) 是把 "任意命令行程序" 注册成
Windows 服务最省事的工具。worker 本身不依赖 NSSM，仅借它做开机自启 + 自动重启。

## 1. 下载 NSSM

从 https://nssm.cc/download 下载并解压到 `C:\nssm\` (或任意目录)。

## 2. 安装服务

在 **管理员 cmd** 里：

```cmd
C:\nssm\win64\nssm.exe install FactorResearchLiveMarket
```

弹出 GUI，按下面填：

| Tab | 字段 | 值 |
|---|---|---|
| **Application** | Path | `C:\factor_research\backend\.venv\Scripts\python.exe` |
| | Startup directory | `C:\factor_research`（项目根） |
| | Arguments | `-m backend.workers.live_market`（要开归档加 ` --archive-1m`） |
| **Details** | Display name | `Factor Research Live Market Worker` |
| | Description | 实盘行情数据采集 |
| | Startup type | Automatic |
| **I/O** | Output (stdout) | `C:\factor_research\logs\live_market.log` |
| | Error (stderr) | `C:\factor_research\logs\live_market.err.log` |
| **Environment** | Environment | （见下，每行一对） |

**Environment 内容（按你的实际值改）：**

```
MYSQL_HOST=127.0.0.1
MYSQL_PORT=3306
MYSQL_USER=myuser
MYSQL_PASSWORD=mypassword
MYSQL_DATABASE=quant_data
CLICKHOUSE_HOST=127.0.0.1
CLICKHOUSE_PORT=9000
CLICKHOUSE_DATABASE=quant_data
```

**Exit actions** tab：保持默认 `Restart application`（即 worker 异常退出自动重启）。
Throttling 设 30s（避免疯狂重启）。

点 **Install service** 完成。

## 3. 启停 / 状态

```cmd
sc start FactorResearchLiveMarket
sc query FactorResearchLiveMarket
sc stop FactorResearchLiveMarket
```

也可以用 NSSM 自己的命令：

```cmd
C:\nssm\win64\nssm.exe start FactorResearchLiveMarket
C:\nssm\win64\nssm.exe stop FactorResearchLiveMarket
C:\nssm\win64\nssm.exe restart FactorResearchLiveMarket
```

## 4. 查日志

```cmd
type C:\factor_research\logs\live_market.log
REM 或者用 PowerShell 实时跟踪：
Get-Content C:\factor_research\logs\live_market.log -Wait -Tail 50
```

## 5. 卸载服务

```cmd
C:\nssm\win64\nssm.exe remove FactorResearchLiveMarket confirm
```

## 6. 常见问题

### 服务起不来

1. 在 cmd 手动跑一次同样命令，确认能正常启动：
   ```cmd
   "C:\factor_research\backend\.venv\Scripts\python.exe" -m backend.workers.live_market --once
   ```
2. 检查路径有没有空格 / 中文（建议项目放在英文路径下）。
3. 检查环境变量里数据库凭据对不对。

### KeyboardInterrupt 退出码奇怪

NSSM 默认把任何非零退出当异常重启。如果你想"用户主动 stop 不重启"，
在 NSSM 的 **Exit actions** tab 把 `Exit code: 0` 设为 `No action`。
worker 收到 SIGTERM（NSSM stop）会走 KeyboardInterrupt 分支返回 0。
