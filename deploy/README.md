# 小主机迁移与公网转发

## 当前交付状态（2026-09-10）

已完成服务切换：公网入口 https://pawe.foxerlove.cn，Web/API/Worker/PostgreSQL 均运行在 OpenWrt，VPS 仅提供 HTTPS 与 SSH 转发。下方“同步与迁入记录”保留分阶段历史，不代表当前仍处于等待恢复状态。

- 经用户明确授权传输运行凭据，目标 `config/runtime.env` 权限 0600；空库事务恢复成功，再升级至 `20260908_0020`。恢复/迁移前后核验 users=1、decision_sets=9、decision_items=45、daily_briefs=20、weekly_reviews=9、weekly_review_items=42，原 AI 凭据记录为 0。
- PostgreSQL/API 健康，Web 仅监听回环 18080，Worker 已显式启用。原 Mac 四个容器保持停止，原数据库卷与备份保留，没有双机调度。
- 公网 HTTPS readiness 200；原管理员登录及身份查询成功、会话 Cookie 为 Secure；匿名身份查询与规则包接口为 401。用户选择自行在用户管理中修改不足 12 位的原密码，本次没有重置密码。
- 2026-09-10 18:00:54（Asia/Shanghai）成功生成 `backups/pawe-20260910T100054Z.dump` 并核验目录清单；每天 03:10 执行 `run-backup.sh`，初始运行密钥另存本机受保护备份文件。尚未建立异机增量备份，单块 SSD 故障仍有风险。
- 修复仅 PAWE 容器的 Nikki fake-IP 路由：`90-pawe-fakeip.nft` 部署至 `/etc/nftables.d/`，依赖现有 fake-IP 池 `198.18.0.0/16`、mark `0x81/0xff` 和路由表 81。未修改 Nikki 配置、原 LAN/WAN 路径或分区，未重启小主机。以后改变 Nikki 地址池/标记时须同步复核此文件。
- 容器内腾讯和新浪均取到 2026-09-08～09 的两根日线；东方财富仍返回 RemoteProtocolError。Worker 启动补跑两只自选时仍报告历史备源覆盖不完整，保留 single_source/失败审计，不降低门禁或宣称日报全部成功。

系统上线不等于所有研究任务成功：仍需处理下列已知限制，并尽快由用户更改管理员密码。

## 已核验与授权

2026-09-08 在用户已登录的 LuCI 只读确认：J4125、x86/64、OpenWrt 25.12.5、内核 6.12.94；总内存 7.62 GiB、当时可用约 7.39 GiB；已连续运行约 21 天。现有 `/dev/sda1` 为 `/boot`，`/dev/sda2` 承载 SquashFS 系统，loop0 的 F2FS `/overlay` 约 7.98 GiB。SSD 256 GB 来自 My-vps 台账，未分配扇区边界尚未通过设备命令核验。

用户允许使用未分配空间、新建分区和挂载点；明确禁止修改现有分区和重启小主机。不能移动、扩缩、格式化 sda1/sda2，不能运行生成并替换全部挂载配置的操作。若内核无法在线识别新分区，停止此步骤，不以重启解决。

2026-09-10 使用用户指定的 SSH 私钥完成只读认证与设备核验：SSD 为 500118192 个 512B 扇区；现有 sda3 占用 16777216–500118158，ext4 挂载于 `/mnt/media`，可用约 225.8 GiB。GPT 可用区间已被现有分区覆盖，没有可新建分区的未分配空间。Docker 尚未安装。没有读取或导出私钥内容，没有修改分区、防火墙或重启设备。

用户随后明确指定使用现有挂载下的 `/mnt/media/projects`，项目目录为 `/mnt/media/projects/PAWE`。此选择替代新建独立分区方案；不扩缩或格式化任何现有分区，不修改现有挂载配置。

2026-09-10 已建立并复核 `/mnt/media/projects/PAWE`：父目录及项目目录均为真实目录、非符号链接，所在挂载仍为 `/dev/sda3` 的 ext4。建目录阶段未修改已有权限或属主；后续文件迁入状态见下节。

## 2026-09-10 同步与迁入记录

- 应用提交 `7c150ded6427e28f462363d216b63c996115f13a` 已推送并与 GitHub `main` 核对一致；代码迁入 `/mnt/media/projects/PAWE/releases/7c150ded6427e28f462363d216b63c996115f13a`，共 260 个追踪文件（含非敏感 `.env.example` 模板），关键源码及部署文件 SHA-256 与提交匹配。未创建 `current` 指针或启动目标服务。
- 原机四个 PAWE 容器原已停止。只短暂启动 PostgreSQL 完成 custom-format 备份，随后停止；原数据库卷保留不变，API/Worker/Web 未启动。
- 备份 `pawe-original-20260909T232522Z.dump` 为 14903834 bytes，SHA-256 `702bab432d2d17715a046ed398d49553a3fafa0475782ac6dc7cf741b6ee4776`；本机受保护临时目录 `/private/tmp/pawe-backup.h4bPEf`，目录 0700、文件 0600。`pg_restore --list` 校验通过（364 项），尚未做恢复演练。临时目录不是长期备份位置，正式切换前必须转存并复核。
- 数据库备份传输曾被安全审批拦截；用户随后以“123确认”明确批准该敏感备份经 SSH 传至指定小主机、Docker 安装及专用网桥/必要转发，以及正式库 0020 迁移。此授权不等于已经执行；只记录备份位置及摘要，不提交真实备份或凭据。
- 后续已完成该备份传输：小主机 `/mnt/media/projects/PAWE/backups/pawe-original-20260909T232522Z.dump`，大小与 SHA-256 和原机一致；目录 0700、文件 0600，未覆盖既有文件。尚未还原数据库、执行 0020 或启动服务。
- Docker 安装及必要专用网络配置已获授权；实际采用下节 VPS 公网反向代理方案，OpenWrt Web 只绑定 `127.0.0.1:18080`，不再使用 LAN 8443 或客户端 VPN。
- 正式库 0020 迁移已获确认，尚未执行。部署 Compose 使用非敏感占位值通过 `config --quiet`，脚本语法与 Git 差异检查通过；这些不替代目标机运行验收。
- 已在开发机从上述提交的干净归档构建三个 `linux/amd64` 镜像：`pawe-host-api:7c150de`（镜像 ID `sha256:469ecbf2bf4e6027a105b5a5a1cca47a584b099da6e54d1d1e6308176d0549e`）、`pawe-host-worker:7c150de`（`sha256:166147940bfb10b1dd6affeb29ffb034d6d619bd395d2b85376add940fa63a76`）、`pawe-host-web:7c150de`（`sha256:cd22c2a35e43a896329458a8c1f8603cf22297a2b7ba57e47f3ed952dccfff59`）。API/Worker 在断网验证容器中 `pip check` 与模块导入通过；镜像尚未传入小主机，未启动正式服务。镜像 ID 不等同于 registry manifest digest，上线时须按实际传输方式解析不可变引用。

## VPS 公网入口与 SSH 隧道（替代客户端 VPN 方案）

用户已明确确认并授权操作两台设备：VPS 仅转发访问，PAWE 的 Web、API、Worker、PostgreSQL 和数据留在 OpenWrt；iPhone/Mac 不需要 VPN 客户端。公网用户经 HTTPS 访问 VPS，再由加密 SSH 隧道到达小主机回环地址。PAWE 在应用层已属于公网可达服务，不能称为“仅私网应用”；保护措施是正式登录、CSRF、Secure Cookie 和边缘限流，而非隐藏域名。

### 已完成

- 新增 `pawe.foxerlove.cn A 39.96.199.24`，RecordId `2097911717350288384`。独立 HTTP-01 webroot `/var/www/pawe-acme` 签发 Let’s Encrypt 证书，到期 `2026-12-09 11:57:57`（Asia/Shanghai）；现有 certbot.timer 与全局 Nginx reload hook 负责续期。
- VPS 独立站点 `/etc/nginx/sites-available/pawe.conf` 使用 `vps-nginx.conf`：HTTPS、登录 6 次/分钟/IP（短突发 5）、普通请求限流、禁用代理缓存和请求/响应落盘缓冲。VPS 不部署 PAWE 代码、数据或数据库；仅保存转发配置、证书及必要基础设施日志。
- OpenWrt 的 `pawe-tunnel` 主动连接 VPS TCP 22，VPS 只监听 `127.0.0.1:18443`，转发至 OpenWrt `127.0.0.1:18080`。严格主机公钥校验，私钥仅在 OpenWrt 受保护目录；VPS 专用用户仅允许指定回环端口的 remote forwarding，禁止会话、TTY、密码、agent、X11 和其他监听地址。修复了公钥文件读取权限后，已验证认证与监听成功。
- OpenWrt Docker 29.6.1/Compose 5.1.4 已安装，默认 dockerd 服务 disabled；专用 `pawe-docker` 使用 SSD 数据根 `/mnt/media/projects/PAWE/docker` 并检查 ext4 挂载。安装包曾自动初始化 `/opt/docker`，随后已停止默认服务；该初始化目录保留未删，正式运行不使用它。
- 新增 `br-pawe` 专用防火墙区，仅放行容器内部与到 WAN 的出站；未开放 WAN/LAN 到 PAWE 的转发。宿主既有 `net.ipv4.ip_forward=1`，daemon 的 `ip-forward:false` 表示不让 Docker 改写这一宿主配置。网关 network 文件 hash 未变，Nikki 仍运行；fw4 检查通过，但既有 Passwall2 include 缺失警告保留，未修改该无关配置。
- 四个 amd64 镜像已离线导入。目标实际镜像 ID：API `sha256:55ef6c3586a87a77137b0d99fbc04a495fc614c8df7a1b0fb71ef37051d77d86`、Worker `sha256:abeb0c576ea8b560f1cb9383d919e72ac68fd7ddeff80d3669c37b3ab70a7fa1`、Web `sha256:951d47a9c817dd75dbaa140d679a876f8effedff785eb2505b4ec3d45bab6778`、PostgreSQL 17.10 `sha256:af194ccf3e2d7fe367012c7b88ce8b816c5c889b18a5b316799a1f0d7eac746a`。不同 Docker 镜像存储可能显示不同层级的 ID，运行配置以目标核验值为准。
- 原有 `pokemon.foxerlove.cn/` 与 `duty.foxerlove.cn/health` 均返回 200；独立 Nginx 配置检查通过，只执行 reload，没有重启设备或原应用。

### 恢复授权历史（已解除）

传输运行凭据最初被安全审批拒绝；用户随后明确“确认允许”，已完成传输、数据库恢复、0020 及服务切换，当前状态以本文顶部为准。旧机器服务保持停止且数据卷保留，不得双机运行正式任务。

### 使用与回退

当前部署文件位于小主机 `/mnt/media/projects/PAWE/deployment/deploy`。`compose.tunnel.yaml` 为独立入口，不与原 LAN HTTPS `compose.yaml` 混用。通过 `create_runtime_env.py` 首次生成环境文件（不覆盖已有文件）；导出受保护环境后，`start-tunnel.sh` 检查挂载和 amd64 镜像并仅启动被动服务。Worker 切换须单独执行 `docker compose -f deploy/compose.tunnel.yaml --profile scheduler up -d worker`。备份脚本由 `PAWE_COMPOSE_FILE=deploy/compose.tunnel.yaml` 选择正确栈。

回退优先在 VPS 关闭 PAWE 专用站点或停止专用隧道，保留原网站；停止新 Worker 后才考虑恢复原机。原机恢复会丢失切换后的新增数据，必须先评估并备份，不直接用旧库覆盖新库。`vps-acme-bootstrap.sh` 是一次性脚本，发现已有 PAWE 站点即停止，不用于日常更新。既有网络、分区和媒体服务不得纳入回退删除范围。

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

## 尚未完成的可靠性加固

- Worker 租约、心跳、卡死检测及任务失联恢复；本次部署文件中的 restart 策略不能代替这些能力。
- 异机受保护备份、新备份的定期恢复演练与整链路压力测试。
- 免费行情备用源历史覆盖不足，腾讯/新浪短窗口连通并不等于全市场与全历史覆盖完整。
- 原管理员密码由用户自行修改；当前不把弱密码风险视为已解决。

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

全历史离线 SQL 生成在既有迁移的 JSONB 字面量处失败；本次 0019→0020 离线 SQL 可生成，实际在线迁移通过。未把该历史限制宣称已修复。上述为第一阶段验证；后续正式切换状态见本文顶部，部署阶段未重复运行无代码变化的完整应用测试。
