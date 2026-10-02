# 集沣 ERP 外网 A/B 测试站

两套工作台共用四个应用、同一个站点、同一个数据库和同一套合成测试资料：

- A：`/desk/jifeng-modern`，优化后的开源 ERP 界面。
- B：`/desk/jifeng-legacy`，沿用旧系统菜单、列表和操作入口。

本发布业务源码固定为 `8858743800efe1836ac53f5c3de6644dab4e7923`。Frappe 16.35.0、ERPNext 16.36.0、HRMS 16.20.0 的源码提交、完整已构建资源及 banking lock 的唯一覆盖记录在发布包 `manifest.json`。共用业务工作台核心，界面不各自计算或存储业务数据。

## 2026-10-02 实际发布

- [A · 开源优化版](https://116.62.241.148/desk/jifeng-modern)
- [B · 旧界面版](https://116.62.241.148/desk/jifeng-legacy)

运行镜像为 `jifeng/erp-test:8858743-test-r4`，ID 为 `sha256:543ecd7e7e51f5088659718cadb950f542a80bd8a17a5a8da92a3067e9372cff`。业务包保持冻结，部署脚本单独记录版本。镜像后的通用启动参数修订未重新导出镜像：服务器 Compose 明确指定 Gunicorn 的 sites 工作目录，并将 `short,default,long` 作为 worker 的单个队列参数；实际后端和 worker 按这些参数运行。后续构建应使用当前 Containerfile 与 Compose。

新建站点 `jifeng-erp-test.localhost` 与数据库 `jifeng_erp_test`；独立原生连接已确认四个应用、设置向导、测试账号业务权限和全部合成资料。测试账号没有 System Manager 角色。种子包含 2 名员工、3 个商品、两项物料的商品工艺档案、电镀厂档案和销售订单草稿；初始化及种子验证时 GL Entry、Stock Ledger Entry 均为 0。

外网真实 Chrome 已通过登录、两套电镀厂查询、19 列展示、A 主页回航保留，以及合成档案备注 A→B、B→A 的两次保存和回读。验证记录保留驱动初期失败及限制，不把一条档案往返等同于所有模块验收。scheduler 原 128 MiB 限额出现历史 OOM，现按 `fcca100` 修订为 RAM 256 MiB、RAM+swap 512 MiB，仅重建调度容器，其他七个容器完整保持。

构建结束后残留的 daemon 内存占用曾引起主机 global OOM，Docker 自重启后已受控恢复现有服务，数据卷及已保存的 A/B 备注经独立连接核验保持。当前八服务 running、restart0，数据库 healthy；两套浏览器实时连接也已复验通过。该主机为 2 CPU、约 2 GB RAM、独立 4 GB swap 的小型测试机，未做并发负载验收。后续镜像在独立构建机生成并验证，按直连要求传输到此运行主机；保留此次失败、恢复与数据验证回执。

`318ce40` 配置本项目 Docker 的 Go 管理内存软限额 `GOMEMLIMIT=512MiB`，以及 `live-restore`。先重载并确认后者真实启用，再仅重启 daemon 使环境生效；独立复核八个容器 ID、PID、StartedAt、重启数保持，containerd 不变，数据库 healthy。运行快照中 daemon RSS 约 505 MiB、swap 约 9.4 MiB；软限额不能当作 RSS 硬上限或并发验收。

外部标准 CA 校验通过，HTTP 登录入口重定向到 HTTPS，HTTPS 登录页与 ping 均为 200。当前 IP 证书到期为 2026-10-09 00:38:58 UTC。自己的续期 timer 已 active/enabled：staging 续期演练退出 0，生产检查及 nginx 重载成功且未额外申请生产证书；尚未经历实际到期窗口续期。详细验收及限制见 [验证记录](../../docs/validation.md)。

## 服务器布局

部署在独立目录 `/opt/jifeng-erp-test`，Compose 项目名为 `jifeng-erp-test`。原服务器定时任务及其文件保持原样。只有前端公开 80、443；数据库、Redis、后端和实时服务均位于私有容器网络。

`releases/8858743-r2` 保存冻结的公开业务源码、资产及 SHA 清单。部署文件按后续工程提交修订，原文件与上传摘要保留在本项目 revision-history 和部署记录中；不能仅凭目录名把部署文件认定为初始字节。`secrets` 保存新生成的数据库、管理员、测试账号密码；不进入镜像、Git、环境变量或命令行参数。`acme` 保存证书及验证目录。测试站不导入本地站点配置、旧库、旧账户密码或登录会话。

## 构建与初始化

镜像原始来源为 Frappe 官方 GHCR 和 MariaDB 官方 GHCR，固定 amd64 manifest digest。官方直连大层连续 EOF 后，使用南京大学公开 GHCR 缓存，逐一验证 manifest digest 与原官方内容一致、实际直连及层 HEAD；禁止自动代理回退。下载前还须验证服务器无代理/TUN及实际出口，上传须核对当前 SSH 连接在 Mihomo 中的 DIRECT 链。

构建上下文只开放 `sources.tar.gz`、`assets.tar.gz`、明确的 banking lock 覆盖和部署源码。先构建 `tls-runtime` 可独立申请证书；正式运行镜像再加入四个应用和锁定 Redis 版本。构建使用原生 Bench 安装 Python/Node 依赖，部署已有完整资源；首次 sites volume 包含明确的相对 assets 链接。

源码/资源 tar 的元数据统一为 uid/gid 0，解包后须在新建的镜像目录中明确恢复 frappe 所有权，并实际读取四个本地 Git tag。Python 官方文件 CDN 直连多次中断后，使用已验证的阿里云 HTTPS PyPI 源；关键 wheel 的镜像 SHA 与官方发布元数据一致。没有配置额外索引或代理回退。

部署源码上传后在服务器为 root 0600；镜像内明确设目录 0755、公开部署源码 0644、入口脚本 0755，使 frappe 可以读取初始化与 seed 脚本。只修改镜像中的公开源码权限，secret 文件不复制进镜像。

`initialize.py` 仅创建不存在的新站；`seed_test_data.py` 使用官方设置向导及正常文档校验创建合成测试资料。失败时先检查初始化及 seed 的阶段记录、站点、数据库和容器退出状态，再决定后续动作，禁止直接 force 或重复未知结果的初始化。

IP HTTPS 证书使用 Certbot 5.4.0 和 Let’s Encrypt shortlived profile。本项目自己的 `jifeng-erp-test-cert-renew.timer` 每天 03、15 时检查，带最多 20 分钟随机延时和 Persistent 补跑；服务调用当前主机 Compose 中的 certbot，再重载 nginx。续期脚本依赖 `releases/8858743-r2/deploy`，保留此目录；更换发布目录时同步修订并核验服务。应用在可信 HTTPS 配置后公开。

## 验收范围

外网可访问和 A/B 数据互通是本次部署门槛。上线测试不代表旧系统全部业务已完成：当前菜单覆盖中仍有业务预览和部分实现，不能把它们作为正式可用功能交付。旧系统全部模块正式可用仍是总目标；模块差距和原生/GUI验收限制继续记录在 `docs/validation.md`。

工程提交不包含密码、私钥、站点文件、数据库、镜像或发布包。远程部署上传与 Git 远程推送是不同操作，本次未推送 Git。

## Docker 内存设置与维护

本机 `/etc/systemd/system/docker.service.d/jifeng-erp-test-memory.conf` 使用 `docker-memory.conf` 的公开设置；环境变量在新 daemon 进程中生效。`docker-daemon-settings.json` 是本机的 live-restore 设置，本次原 `/etc/docker/daemon.json` 不存在，按该文件新建；已有配置时合并该字段并保留其他键和原文件记录。

先验证 daemon 配置并 reload，再从实际 `docker info` 确认 LiveRestoreEnabled=true；需要重启 daemon 时保持版本、数据目录、存储驱动和网络参数，核 systemd 的 KillMode=process、Delegate=yes，保持 containerd。重启后比较容器 ID、PID、StartedAt、重启数及实际业务健康。Go 软内存限额的行为按 [Go 官方说明](https://go.dev/doc/gc-guide#Memory_limit)，live-restore 的条件按 [Docker 官方说明](https://docs.docker.com/engine/daemon/live-restore/)；发生异常先核本项目操作记录和当前状态。
