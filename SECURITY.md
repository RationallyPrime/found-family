# Security Policy

## Supported versions

Security fixes are applied to the current `main` branch. Releases prior to the
latest commit on `main` are not maintained unless a release announcement says
otherwise.

## Reporting a vulnerability

Do not open a public issue for a suspected vulnerability or exposed secret.
Use the repository's private GitHub Security Advisory reporting flow instead.
Include the affected revision, deployment shape, reproduction steps, expected
impact, and any evidence that credentials or private memories were accessed.

We aim to acknowledge a report within three business days, provide an initial
severity assessment within seven business days, and coordinate disclosure only
after a fix or containment plan exists.

## Deployment assumptions

- The API host port must remain bound to loopback and be reached remotely only
  through the authenticated Cloudflare Tunnel.
- Neo4j must never publish its Browser or Bolt ports in production.
- `.env`, tunnel credentials, observability credentials, graph backups, and MCP
  credentials are secrets. Store them with owner-only permissions and rotate
  them immediately after suspected disclosure.
- Protect the OAuth authorization endpoint with its dedicated owner credential;
  never reuse the Neo4j password or JWT signing key for interactive login.
- Production deployments must pass the repository quality, container, and
  vulnerability gates before rollout.

## Scanner exceptions

Source and first-party application image scans block every HIGH or CRITICAL
finding without exceptions. The separately pinned Neo4j vendor image is scanned
with `--ignore-unfixed`; `.trivyignore.yaml` then scopes each temporary exception
to the exact affected package URL and expires it on 2026-08-15.

The accepted Neo4j findings are limited to kernel headers, which are not the
kernel executed by a container, and a shaded Parquet Jackson dependency. Memory
Palace does not expose Parquet ingestion, Neo4j has no published production
ports, and both exceptions must be removed when the vendor image is refreshed.

## Incident response

Contain first: disable public ingress, rotate affected credentials, preserve
logs without copying private memory content into tickets, and take a verified
backup before graph repair. Record the affected commit and image identity so a
clean rebuild and post-incident review are reproducible.
