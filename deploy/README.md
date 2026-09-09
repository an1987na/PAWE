# 小主机迁移：第一阶段实现及执行边界

## 已核验与授权

2026-09-08 在用户已登录的 LuCI 只读确认：J4125、x86/64、OpenWrt 25.12.5、内核 6.12.94；总内存 7.62 GiB、当时可用约 7.39 GiB；已连续运行约 21 天。现有 `/dev/sda1` 为 `/boot`，`/dev/sda2` 承载 SquashFS 系统，loop0 的 F2FS `/overlay` 约 7.98 GiB。SSD 256 GB 来自 My-vps 台账，未分配扇区边界尚未通过设备命令核验。

用户允许使用未分配空间、新建分区和挂载点；明确禁止修改现有分区和重启小主机。不能移动、扩缩、格式化 sda1/sda2，不能运行生成并替换全部挂载配置的操作。若内核无法在线识别新分区，停止此步骤，不以重启解决。

2026-09-10 使用用户指定的 SSH 私钥完成只读认证与设备核验：SSD 为 500118192 个 512B 扇区；现有 sda3 占用 16777216–500118158，ext4 挂载于 `/mnt/media`，可用约 225.8 GiB。GPT 可用区间已被现有分区覆盖，没有可新建分区的未分配空间。Docker 尚未安装。没有读取或导出私钥内容，没有修改分区、防火墙或重启设备。

用户随后明确指定使用现有挂载下的 `/mnt/media/projects`，项目目录为 `/mnt/media/projects/PAWE`。此选择替代新建独立分区方案；不扩缩或格式化任何现有分区，不修改现有挂载配置。

2026-09-10 已建立并复核 `/mnt/media/projects/PAWE`：父目录及项目目录均为真实目录、非符号链接，所在挂载仍为 `/dev/sda3` 的 ext4。仅确保目录存在，未修改已有权限或属主，尚未传入项目文件或启动服务。

## 文件与使用

- `preflight.sh`：只读硬件、挂载和 Docker 盘点，不安装软件。
- `compose.yaml`：独立于桌面 Compose 的部署配置；仅暴露指定地址的 HTTPS 端口，数据库不暴露宿主端口，限制资源和日志大小；Worker 位于显式 `scheduler` profile，默认不启动。
- `web.Dockerfile` / `nginx.conf`：锁文件安装前端，同源 API 反向代理，使用已有受信任证书；不通过浏览器绕过证书警告。
- `start.sh`：要求独立 ext4 挂载已存在、数据目录位于其中、镜像引用带 digest；只启动被动服务。只针对 Linux/OpenWrt 宿主执行，不在 Mac 上执行。
- `backup.sh`：导出 PostgreSQL custom-format 备份并检查目录清单，已有备份不覆盖；该检查不能替代恢复演练。备份与加密密钥应另有独立介质上的受保护副本，不自动删除旧备份。

所有脚本都使用 `sh deploy/<name>.sh` 执行。运行位置为仓库根目录。Compose 变量应由受保护的部署环境提供：`PAWE_POSTGRES_PASSWORD`（使用 URL-safe 高熵随机值）、`PAWE_AI_CREDENTIAL_ENCRYPTION_KEY`、`PAWE_ENV_FILE`（绝对路径）、`PAWE_DATA_DIR`、`PAWE_STORAGE_MOUNT`、`PAWE_TLS_DIR`、`PAWE_BIND_IP`、`PAWE_HTTPS_PORT`（默认 8443）、三个 `PAWE_*_IMAGE` digest；备份另需 `PAWE_BACKUP_DIR`。保护 env 文件权限，禁止提交真实值或输出展开后的 Compose 配置。

镜像应在开发/构建机为 linux/amd64 构建并完成验证后传入小主机，不在网关上大规模构建。Web 使用 `docker build -f deploy/web.Dockerfile`；API/Worker 使用原 Dockerfile。后端完整依赖锁定和镜像构建供应链加固仍待完成，不能把本配置视作已经生产验收。

## 无重启存储方案

1. 每次部署前确认 `/mnt/media` 仍为现有 sda3 的 ext4 挂载，避免挂载丢失时写入系统盘。
2. 项目根目录使用 `/mnt/media/projects/PAWE`；检查父目录与目标不是符号链接，不覆盖已有文件或更改已有目录权限。
3. 后续数据目录拟使用 `/mnt/media/projects/PAWE/data`，对应 `PAWE_STORAGE_MOUNT=/mnt/media`、`PAWE_DATA_DIR=/mnt/media/projects/PAWE/data`。配置、备份及 Docker 数据根应分别隔离；目录建立不代表数据迁移或服务上线。
4. Docker 安装与网络集成先评估对网关防火墙、代理和 IPv6 的影响；不修改现有网络路径，不通过主机重启完成安装。

## 尚未完成的上线前置条件

- 小主机 Docker 与证书配置；SSH 及现有存储挂载已核验，不再计划新建分区。
- Worker 租约、心跳、卡死检测及任务失联恢复；本次部署文件中的 restart 策略不能代替这些能力。
- 原数据库及 AI 凭据加密兼容迁移、备份恢复演练、数据源出口验证和整链路压力测试。
- 正式切换：停旧 Worker 和写入、最终同步、校验后才启用新 `scheduler` profile。不能双机同时运行正式任务。
- 本次规则包迁移 `20260908_0020` 仅在独立测试库执行；正式库执行另需明确确认。

## 规则版本第一阶段

页面入口：规则实验 → 规则版本。API：GET/POST `/api/v1/rule-packages`、GET `/{id}/download`、POST `/{id}/validate`。仅管理员可访问，写入要求 CSRF；上传按规范 JSON 处理，限制流式请求大小和源文档总量，不解压文件、不执行上传代码；同版本不同内容返回 409，相同内容重复返回同一条记录。上传与原始校验证据保持不变，重新校验只返回新版检查结果。

离线导入工具（明确指定本机源目录，服务运行时不依赖旧项目）：

```sh
PYTHONPATH=apps/api .venv/bin/python -m scripts.export_rule_package \
  --source /Users/fengzhu/Documents/Projects/pick_a_weekly/outputs \
  --version pick-weekly-v9-candidate-20260906 \
  --output /path/to/new-package.json
```

导出只读取 ff_rules.md、rule_playbook.md、rule_data_contract.md、rule_experiment.md，不读取凭据、AGENTS 工具权限或任意脚本。实际候选包 hash 为 `439088a9c92c3eb31b5594305755f2092be51e5a1f1b58da6f45ffa810738d36`，仅导入测试库。

正式/回溯计算开始经显式引擎注册入口运行，但目前只注册原 `v9.0.0`，回归测试验证结果不变。尚未实现候选特征适配、最终组合选择、自动实验运行、批准激活与回滚接口；页面明确展示阻塞，不冒称上传即更新。下一阶段先实现纯候选适配和隔离实验，再逐项确认固定五只、过热补足、前瞻资格、非完整周的规则冲突。正式规则、已发布名单和历史指标保持不变。

## 验证记录

后端 310 项测试通过，Mypy 94 文件、Ruff 通过；前端 19 项测试、构建通过，新 Web 部署镜像独立构建通过（不是目标设备部署验证）。临时 PostgreSQL 完成全量在线迁移及 0020 downgrade/upgrade；真实 API 验证上传、幂等、内容冲突、CSRF、普通用户拒绝、大小和字段限制。真实浏览器验证桌面/390px 移动端正常上传及非法包错误，移动端无横向溢出；普通用户无管理入口。验收同时修复退出后保留上一账号管理页标题的状态残留。

全历史离线 SQL 生成在既有迁移的 JSONB 字面量处失败；本次 0019→0020 离线 SQL 可生成，实际在线迁移通过。未把该历史限制宣称已修复。当前代码未部署至正式 API/Web，也未推送 GitHub。
