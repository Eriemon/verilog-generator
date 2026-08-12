# Security Policy

## Supported Versions

Security fixes target the latest `main` branch unless a release branch is explicitly announced.

## Reporting a Vulnerability

Please report security issues through GitHub private vulnerability reporting if it is enabled for this repository. If that is unavailable, open a minimal public issue that requests a private coordination channel and does not include exploit details, secrets, or private infrastructure information.

## What Counts

- Secret exposure, credential leakage, or unsafe logging.
- Path traversal or unsafe artifact extraction behavior.
- Untrusted command execution through model/provider hooks.
- Validation logic that can falsely report simulator or implementation acceptance.
- Documentation that encourages unsafe token, SSH, or remote-server handling.

## Handling Expectations

We will acknowledge valid reports, reproduce them in a minimal environment, and publish fixes with clear notes. Do not include real tokens, private keys, proprietary hardware designs, private server names, or private network details in a report.

