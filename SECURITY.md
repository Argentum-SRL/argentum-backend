# Security Policy

## Overview

Security is a priority for Argentum.

We take reports of security vulnerabilities seriously and appreciate responsible disclosure from security researchers, users, developers, and other members of the community.

This policy explains which versions are supported, how to report a vulnerability, what information to include, and how we handle security reports.

## Supported Versions

The `main` branch represents the currently supported development and production codebase.

| Version / Branch | Supported |
| ---------------- | --------- |
| `main`           | :white_check_mark: |
| Older versions   | :x: |

Security fixes are applied to the currently supported codebase. Older releases or forks may not receive security updates.

## Reporting a Vulnerability

If you believe you have found a security vulnerability in Argentum, please report it privately.

Please do not disclose the vulnerability publicly before we have had an opportunity to investigate and address it.

### Preferred reporting method

Use GitHub's private vulnerability reporting / Security Advisories feature available from this repository's Security section.

When possible, please provide:

- A clear description of the vulnerability.
- The affected component, endpoint, feature, or file.
- The steps required to reproduce the issue.
- The potential security impact.
- Any proof of concept that helps demonstrate the issue.
- Relevant logs, screenshots, requests, or responses, when safe to provide.
- Any suggested mitigation or remediation.
- Your contact information if you would like to receive follow-up.

Please avoid including real passwords, authentication tokens, API keys, personal information, financial information, or other sensitive data in the report.

## What to Expect

After receiving a valid security report, we will:

1. Review and triage the report.
2. Attempt to reproduce and validate the reported issue.
3. Assess its security impact and severity.
4. Determine whether remediation is required.
5. Apply an appropriate fix or mitigation when necessary.
6. Communicate relevant status updates when additional information is required or when the investigation materially changes.

We aim to acknowledge security reports as soon as reasonably possible.

Response and remediation timelines may vary depending on the severity, complexity, reproducibility, and operational impact of the vulnerability.

## Vulnerability Severity

Security issues may be classified according to their potential impact and exploitability.

Examples include:

- Critical: vulnerabilities that could result in severe compromise of the application, infrastructure, authentication system, or sensitive data.
- High: vulnerabilities that could allow significant unauthorized access, privilege escalation, data exposure, or other serious impact.
- Medium: vulnerabilities with meaningful but more limited security impact.
- Low: vulnerabilities with limited security impact or requiring unusual conditions to exploit.

Severity is determined by Argentum based on the actual circumstances and potential impact of the reported issue.

## Responsible Disclosure

We ask security researchers to:

- Report vulnerabilities privately.
- Give us reasonable time to investigate and remediate the issue.
- Avoid accessing, modifying, deleting, or exfiltrating data that does not belong to them.
- Avoid actions that could degrade or interrupt Argentum services.
- Avoid social engineering, phishing, denial-of-service attacks, or physical attacks against Argentum personnel or infrastructure.
- Stop testing if sensitive user data is encountered and report the finding instead.

Please do not publicly disclose the vulnerability, exploit details, credentials, tokens, personal information, or other sensitive information before coordinated disclosure.

## Testing and Production Systems

Only test systems and functionality that you are authorized to test.

Do not intentionally:

- Access another user's account or data.
- Modify or delete production data.
- Download or retain sensitive information.
- Perform destructive testing.
- Conduct denial-of-service or resource-exhaustion attacks.
- Attempt to compromise third-party services or infrastructure.
- Use vulnerabilities to move laterally into systems that are outside the scope of Argentum.

If you accidentally gain access to sensitive information, stop testing, do not copy or distribute it, and report the issue privately.

## Security Secrets

Never include secrets or credentials in a security report unless they are strictly necessary to demonstrate the vulnerability.

If a report contains a potentially valid secret, token, API key, password, or credential, please identify it without unnecessarily reproducing or sharing the value.

Argentum may rotate or revoke credentials when a potentially compromised secret is reported.

## Scope

This policy applies to security vulnerabilities affecting Argentum's publicly available source code and services operated by Argentum.

Third-party services, dependencies, hosting providers, external integrations, and infrastructure not controlled by Argentum may have their own security reporting procedures.

If you are unsure whether a system is in scope, report the issue privately and explain the affected component.

## Out of Scope

The following generally do not qualify as security vulnerabilities unless they demonstrate a meaningful security impact:

- Issues that require physical access to a user's device.
- Self-XSS without a realistic exploitation path.
- Spam or content-only issues.
- Reports based solely on outdated software versions that are no longer supported.
- Automated scanner output without a demonstrated vulnerability.
- Missing security headers without a practical security impact.
- Rate-limit observations without meaningful abuse potential.
- Best-practice recommendations that do not represent an exploitable vulnerability.
- Vulnerabilities exclusively affecting third-party services.

We may still review reports outside these categories when they demonstrate a significant security risk.

## Coordinated Disclosure

After a vulnerability has been investigated and, when appropriate, remediated, Argentum may coordinate public disclosure with the reporter.

Public disclosure should be coordinated in advance when the issue could affect Argentum users, infrastructure, or other parties.

We may credit security researchers who responsibly report valid vulnerabilities, unless they prefer to remain anonymous.

## Safe Harbor

We consider security research conducted in good faith and in accordance with this policy to be authorized activity.

We will not pursue legal action against researchers who:

- Follow this policy.
- Avoid accessing or modifying data that does not belong to them.
- Avoid service disruption.
- Report vulnerabilities privately and responsibly.
- Stop testing and notify us when they encounter sensitive information.

This safe harbor does not authorize activity that violates applicable law, third-party terms, or systems outside Argentum's control.

## Security Updates

Security fixes may be released through normal software development and deployment processes.

Depending on the nature of the vulnerability, remediation may include:

- Code changes.
- Configuration changes.
- Credential rotation.
- Dependency updates.
- Infrastructure changes.
- Additional monitoring or controls.
- User notifications when appropriate.

## Contact

For security-related reports, use GitHub's private vulnerability reporting / Security Advisories mechanism for this repository whenever available.

For general support requests, please use the normal Argentum support channels rather than this security reporting process.

Thank you for helping keep Argentum and its users secure.
