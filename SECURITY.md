# Security Policy / 安全策略

## Supported Versions

Security fixes target the latest `main` branch unless a release branch is explicitly announced.

安全修复默认面向最新的 `main` 分支，除非项目明确声明维护某个发布分支。

## Reporting a Vulnerability

Please report security issues through GitHub private vulnerability reporting if it is enabled for this repository. If that is unavailable, open a minimal public issue that requests a private coordination channel and does not include exploit details, secrets, or private infrastructure information.

如果仓库启用了 GitHub 私密漏洞报告，请优先使用该渠道。如果不可用，请创建一个最小化公开 issue，请求私下协调，但不要包含利用细节、密钥或私有基础设施信息。

## What Counts

- Secret exposure, credential leakage, or unsafe logging.
- Path traversal or unsafe artifact extraction behavior.
- Untrusted command execution through model/provider hooks.
- Validation logic that can falsely report simulator or implementation acceptance.
- Documentation that encourages unsafe token, SSH, or remote-server handling.

## Handling Expectations

We will acknowledge valid reports, reproduce them in a minimal environment, and publish fixes with clear notes. Do not include real tokens, private keys, or proprietary hardware designs in a report.

