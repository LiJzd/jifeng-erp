# 集沣 ERP 外网 A/B 测试站

两套工作台共用四个应用、同一个站点、同一个数据库和同一套合成测试资料：

- A：`/desk/jifeng-modern`，优化后的开源 ERP 界面。
- B：`/desk/jifeng-legacy`，沿用旧系统菜单、列表和操作入口。

本发布业务源码固定为 `8858743800efe1836ac53f5c3de6644dab4e7923`。Frappe 16.35.0、ERPNext 16.36.0、HRMS 16.20.0 的源码提交、完整已构建资源及 banking lock 的唯一覆盖记录在发布包 `manifest.json`。共用业务工作台核心，界面不各自计算或存储业务数据。

## 服务器布局

部署在独立目录 `/opt/jifeng-erp-test`，Compose 项目名为 `jifeng-erp-test`。原服务器定时任务及其文件保持原样。只有前端公开 80、443；数据库、Redis、后端和实时服务均位于私有容器网络。

`releases/8858743-r2` 保存冻结的公开源码、资产、SHA 清单和部署文件。`secrets` 保存新生成的数据库、管理员、测试账号密码；不进入镜像、Git、环境变量或命令行参数。`acme` 保存证书及验证目录。测试站不导入本地站点配置、旧库、旧账户密码或登录会话。

## 构建与初始化

镜像原始来源为 Frappe 官方 GHCR 和 MariaDB 官方 GHCR，固定 amd64 manifest digest。官方直连大层连续 EOF 后，使用南京大学公开 GHCR 缓存，逐一验证 manifest digest 与原官方内容一致、实际直连及层 HEAD；禁止自动代理回退。下载前还须验证服务器无代理/TUN及实际出口，上传须核对当前 SSH 连接在 Mihomo 中的 DIRECT 链。

构建上下文只开放 `sources.tar.gz`、`assets.tar.gz`、明确的 banking lock 覆盖和部署源码。先构建 `tls-runtime` 可独立申请证书；正式运行镜像再加入四个应用和锁定 Redis 版本。构建使用原生 Bench 安装 Python/Node 依赖，部署已有完整资源；首次 sites volume 包含明确的相对 assets 链接。

源码/资源 tar 的元数据统一为 uid/gid 0，解包后须在新建的镜像目录中明确恢复 frappe 所有权，并实际读取四个本地 Git tag。Python 官方文件 CDN 直连多次中断后，使用已验证的阿里云 HTTPS PyPI 源；关键 wheel 的镜像 SHA 与官方发布元数据一致。没有配置额外索引或代理回退。

部署源码上传后在服务器为 root 0600；镜像内明确设目录 0755、公开部署源码 0644、入口脚本 0755，使 frappe 可以读取初始化与 seed 脚本。只修改镜像中的公开源码权限，secret 文件不复制进镜像。

`initialize.py` 仅创建不存在的新站；`seed_test_data.py` 使用官方设置向导及正常文档校验创建合成测试资料。失败时先检查初始化及 seed 的阶段记录、站点、数据库和容器退出状态，再决定后续动作，禁止直接 force 或重复未知结果的初始化。

IP HTTPS 证书使用 Certbot 5.4.0 和 Let’s Encrypt shortlived profile。应在发布后安装本项目自己的续期 timer，每天检查两次，并验证实际续期路径和 nginx 重载。应用须在可信 HTTPS 配置后公开。

## 验收范围

外网可访问和 A/B 数据互通是本次部署门槛。上线测试不代表旧系统全部业务已完成：当前菜单覆盖中仍有业务预览和部分实现，不能把它们作为正式可用功能交付。旧系统全部模块正式可用仍是总目标；模块差距和原生/GUI验收限制继续记录在 `docs/validation.md`。

工程提交不包含密码、私钥、站点文件、数据库、镜像或发布包。远程部署上传与 Git 远程推送是不同操作，本次未推送 Git。
