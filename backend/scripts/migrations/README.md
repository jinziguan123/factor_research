# MySQL 增量迁移

本项目早期靠 `init_mysql.sql` 的 `CREATE TABLE IF NOT EXISTS` 做幂等初始化。
**已有 DB** 不会被 CREATE 触发修改，当表加字段时需要人工跑一次 ALTER。

## 使用方式

按文件名数字顺序（001_、002_、…）执行，每条迁移脚本幂等：

```bash
# 本地开发库
mysql -h 127.0.0.1 -u myuser -pmypassword quant_data < backend/scripts/migrations/001_xxx.sql
```

## 文件命名

`NNN_<short_description>.sql`，NNN 三位递增序号。每次 schema 变更都新增一个文件，
不修改已有文件（保证别人的库升级不会漏步骤）。
