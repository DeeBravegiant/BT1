### Title
Docker Containers Running as Root Enabling Privilege Escalation in MPC Node and TEE Launcher - (File: `deployment/Dockerfile-node`, `deployment/Dockerfile-node-gcp`, `deployment/Dockerfile-rust-launcher`)

---

### Summary

All three production Dockerfiles in this repository lack a `USER` directive, causing the MPC node and TEE launcher containers to run as root by default. This is the exact same vulnerability class as the external report. In the context of an MPC network handling threshold key shares and TEE attestation, running as root materially increases the blast radius of any container-level compromise.

---

### Finding Description

None of the three production Dockerfiles specify a non-root user:

- `deployment/Dockerfile-node` (lines 1–21): Sets `WORKDIR /app`, copies the `mpc-node` binary and `start.sh`, but never issues a `USER` instruction. The node process — which participates in threshold signing and holds key share material — runs as UID 0.
- `deployment/Dockerfile-node-gcp` (lines 1–21): Identical structure to `Dockerfile-node`; same omission.
- `deployment/Dockerfile-rust-launcher` (lines 1–18): Runs the `tee-launcher` binary as root **and** installs `docker-cli` and `docker-compose` inside the image. The launcher manages TEE attestation and spawns child containers. Running it as root with Docker CLI access means a compromised launcher process has unrestricted control over the Docker daemon and the host. [1](#0-0) [2](#0-1) 

---

### Impact Explanation

**Medium Risk** (matching the external report's classification), with a potential escalation path to Critical.

- A process-level exploit (e.g., memory-safety bug triggered by a malicious signing payload or malformed P2P message) inside the `mpc-node` container yields a root shell rather than a restricted user shell.
- From root inside the container, standard container-escape techniques (e.g., abusing mounted host paths, `/proc/1/root`, or `CAP_SYS_ADMIN`) become viable, potentially exposing the host filesystem where key shares or TEE secrets are persisted.
- For `Dockerfile-rust-launcher` specifically: root + `docker-cli` inside the container means a compromised launcher can re-launch containers with altered images or modified attestation parameters, undermining the TEE measurement chain that the on-chain `TeeState` / `ContractExpectedMeasurements` governance relies on. [3](#0-2) 

---

### Likelihood Explanation

**Medium.** The MPC node is a long-running network service that:
1. Accepts external signing requests routed from the NEAR contract.
2. Communicates over a TLS mesh with other participants.
3. Processes foreign-chain RPC responses.

Each of these surfaces is attacker-influenced. A single memory-safety or parsing bug in any of these paths, combined with the container running as root, converts a contained process compromise into a full host compromise and potential key-share exfiltration. The TEE launcher's Docker CLI access further amplifies the risk.

---

### Recommendation

Add a `USER` directive to all three Dockerfiles. Create a dedicated non-root user, transfer ownership of `/app` and any data directories to that user, and switch to it before the `CMD`:

```dockerfile
RUN useradd --no-create-home --shell /bin/false mpcnode
RUN chown -R mpcnode:mpcnode /app
USER mpcnode
CMD ["/app/start.sh"]
```

For `Dockerfile-rust-launcher`, additionally audit whether `docker-cli`/`docker-compose` must remain in the image at runtime, or whether they can be removed after the launch phase to reduce the attack surface.

---

### Proof of Concept

1. Build and run `deployment/Dockerfile-node` as-is.
2. Inside the running container, execute `id` → output is `uid=0(root) gid=0(root)`.
3. The `mpc-node` binary and all key-share files under `/app` are owned and writable by root.
4. Any code-execution primitive in the node process (malicious sign payload, P2P deserialization bug, etc.) immediately yields a root shell with no privilege boundary between the compromised process and the host. [4](#0-3) [5](#0-4) [6](#0-5)

### Citations

**File:** deployment/Dockerfile-node (L1-21)
```text
FROM debian:bookworm-slim@sha256:acd98e6cfc42813a4db9ca54ed79b6f702830bfc2fa43a2c2e87517371d82edb
ENV DEBIAN_FRONTEND=noninteractive

RUN \
    --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt,sharing=locked \
    --mount=type=bind,source=./deployment/repro-sources-list.sh,target=/usr/local/bin/repro-sources-list.sh \
    repro-sources-list.sh && \
    apt-get update && \
    apt-get install -y --no-install-recommends ca-certificates openssl python3 && \
    : "Clean up for improving reproducibility" && \
    rm -rf /var/log/* /var/cache/ldconfig/aux-cache

ENV SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt
WORKDIR /app
COPY target/reproducible/mpc-node mpc-node
# The chmod part is also necessary for reproducibility
COPY --chmod=0755 deployment/start.sh /app/start.sh
# Copy embedded localnet genesis file
COPY --chmod=0755 deployment/localnet/genesis.json /app/localnet-genesis.json
CMD [ "/app/start.sh" ]
```

**File:** deployment/Dockerfile-rust-launcher (L1-18)
```text
FROM debian:trixie-slim@sha256:26f98ccd92fd0a44d6928ce8ff8f4921b4d2f535bfa07555ee5d18f61429cf0c
ENV DEBIAN_FRONTEND=noninteractive

RUN \
    --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt,sharing=locked \
    --mount=type=bind,source=./deployment/repro-sources-list.sh,target=/usr/local/bin/repro-sources-list.sh \
    repro-sources-list.sh && \
    apt-get update && \
    apt-get install -y --no-install-recommends ca-certificates docker-cli docker-compose && \
    : "Clean up for improving reproducibility" && \
    rm -rf /var/log/* /var/cache/ldconfig/aux-cache

ENV SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt
WORKDIR /app
COPY --chmod=0755 target/reproducible/tee-launcher tee-launcher
RUN mkdir -p /app-data && mkdir -p /mnt/shared
CMD ["/app/tee-launcher"]
```

**File:** deployment/Dockerfile-node-gcp (L15-21)
```text
WORKDIR /app
COPY target/reproducible/mpc-node mpc-node
# The chmod part is also necessary for reproducibility
COPY --chmod=0755 deployment/start.sh /app/start.sh
# Copy embedded localnet genesis file
COPY --chmod=0755 deployment/localnet/genesis.json /app/localnet-genesis.json
CMD [ "/app/start.sh" ]
```
