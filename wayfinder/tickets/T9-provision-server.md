---
name: T9-provision-server
title: 云服务器开通与初始化
type: task
status: closed
blocked-by: [T8-cloud-choice]
---

## Resolution

**Out of scope** — 用户已有云服务器并自行运维，开通与初始化不在本 effort 内。
移至 MAP.md 的 Out of scope 区。

---

## Question (存档)

把 T8 选定的云服务器实际开通并初始化到"可以部署应用"的状态。

清单（用户手工或半自动）：
- [ ] 选购实例、完成实名/支付
- [ ] 设置安全组：放行 SSH(22)、应用端口（如 8501 Streamlit / 8000 FastAPI）、关闭其他
- [ ] SSH 密钥对 vs 密码登录（推荐密钥）
- [ ] 系统更新 `apt update && apt upgrade`
- [ ] 安装基础：Python 3.11、pip、git、venv、nginx（可选）
- [ ] 创建非 root 用户用于部署
- [ ] 配防火墙 ufw
- [ ] 申请域名 + 备案（如需对外长期暴露，国内 80/443 必须备案）

输出：服务器 IP、SSH 方式、初始用户、已装软件清单。
决议落到 ticket resolution comment，关闭后写进 MAP.md。
