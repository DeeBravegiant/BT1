# Q3284: ZK proof public-private blob downgrade in zk_util.h

## Question
Can an unprivileged attacker enter through `coinbase::api::tdh2::verify` with public_key, ciphertext, and label when public extraction is compared with signing output, reach `include-internal/cbmpc/internal/zk/zk_util.h` `zk_util module`, and use scalar-detached public blob edited to look like a full signing key blob to bypass the requirement that redacted blobs are tagged and rejected by sign/refresh until attach succeeds, causing signing or refresh uses absent, stale, or attacker-supplied private scalar and producing an in-scope cb-mpc bounty impact?

## Target
- File/function: `include-internal/cbmpc/internal/zk/zk_util.h::zk_util module`
- Entrypoint: `coinbase::api::tdh2::verify via include/cbmpc/api/tdh2.h`
- Attacker controls: public_key, ciphertext, and label; specifically scalar-detached public blob edited to look like a full signing key blob when public extraction is compared with signing output
- Exploit idea: Start from supported public API `coinbase::api::tdh2::verify` in `include/cbmpc/api/tdh2.h` with public_key, ciphertext, and label when public extraction is compared with signing output. The malicious side supplies scalar-detached public blob edited to look like a full signing key blob. Investigate whether `include-internal/cbmpc/internal/zk/zk_util.h` `zk_util module` assumes redacted blobs are tagged and rejected by sign/refresh until attach succeeds was already enforced and therefore lets signing or refresh uses absent, stale, or attacker-supplied private scalar.
- Invariant to test: The ZK proof path must preserve curve, key/blob version, party identity, session or label context, access-structure semantics, and validated encodings from `coinbase::api::tdh2::verify` through `include-internal/cbmpc/internal/zk/zk_util.h`.
- Expected Immunefi impact: Coinbase cb-mpc bounty (HackerOne, not Immunefi): Critical key compromise or significant disclosure/substitution of sensitive key material through supported public APIs.
- Fast validation: Write a local public-API harness with one honest unmodified party and malicious fake transport or buffers; mutate scalar-detached public blob edited to look like a full signing key blob; assert rejection before `include-internal/cbmpc/internal/zk/zk_util.h` `zk_util module` can produce a valid-looking signature, key blob, proof, ciphertext, plaintext, public share, or recovered scalar.
