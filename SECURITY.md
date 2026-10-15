# Security policy

## Reporting a vulnerability

Please report security problems **privately** using GitHub's
[private vulnerability reporting](../../security/advisories/new) for this
repository, not a public issue. Include steps to reproduce and the Lemur
version (Settings → About). We'll acknowledge the report and keep you updated
while we work on a fix.

## How Lemur is designed

Lemur is a local desktop app with no accounts, servers or telemetry. Its
interface talks to a backend that runs on your own computer and listens only on
the loopback address (127.0.0.1). That backend refuses requests that don't come
from Lemur itself — requests from other websites (cross-site requests, foreign
origins, or DNS-rebinding attempts) are rejected.

## Supported versions

Security fixes go into the latest release. Installed copies update themselves
on Windows; on macOS, install the latest release from the Releases page.
