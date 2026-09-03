# 0002: Keep Local Compose Networking Private and Portable

## Status

Accepted

## Context

The PDF Kit Compose stack is intended for single-user local use. Publishing the web and Redis ports on every host interface exposed both services beyond the Mac when the container runtime allowed LAN forwarding. Redis never needed a host port. OrbStack also provides automatic Compose domains and HTTPS, while other Docker runtimes still need a predictable localhost URL.

## Decision

The web fallback binds only to `127.0.0.1:8100`, and Redis stays on the internal Compose network. Web and worker startup wait for Redis health, while the web service reports health through the existing `/health` route. OrbStack routes `web.pdf-toolkit.orb.local` explicitly to container port `8000`; the Compose file does not force a CPU architecture or require OrbStack for ordinary Docker use.

Project-level networking is authoritative. The stack does not modify OrbStack's global LAN forwarding or startup settings because those settings affect unrelated projects.

## Rejected Alternatives

- Removing the localhost port would make the stack needlessly OrbStack-specific.
- Publishing Redis would expand the local attack surface without serving an application requirement.
- Forcing `linux/arm64` would prevent the same Compose file from running natively on Intel or Windows hosts.
- Changing OrbStack's global LAN setting would unexpectedly alter other stacks.

## Consequences

OrbStack users can open `https://web.pdf-toolkit.orb.local` without a port number. Other local Docker users use `http://127.0.0.1:8100`. Remote access requires an explicit deployment override or a host reverse proxy, and Redis is reachable only by services on the Compose network.
