# Q20: MPC transport session replay in job.h

## Question
Can an unprivileged attacker enter through `coinbase::api::ecdsa_2p::sign` with key_blob, msg_hash, sid, and malicious two-party transcript when public extraction is compared with signing output, reach `include/cbmpc/core/job.h` `send`, and use a reused sid, aux value, or transcript fragment from a concurrent execution to bypass the requirement that session and aux values are domain-separated by protocol, round, party set, curve, and subproof, causing replayed commitments, proofs, or messages are accepted in another execution and producing an in-scope cb-mpc bounty impact?

## Target
- File/function: `include/cbmpc/core/job.h::send`
- Entrypoint: `coinbase::api::ecdsa_2p::sign via include/cbmpc/api/ecdsa_2p.h`
- Attacker controls: key_blob, msg_hash, sid, and malicious two-party transcript; specifically a reused sid, aux value, or transcript fragment from a concurrent execution when public extraction is compared with signing output
- Exploit idea: Start from supported public API `coinbase::api::ecdsa_2p::sign` in `include/cbmpc/api/ecdsa_2p.h` with key_blob, msg_hash, sid, and malicious two-party transcript when public extraction is compared with signing output. The malicious side supplies a reused sid, aux value, or transcript fragment from a concurrent execution. Investigate whether `include/cbmpc/core/job.h` `send` assumes session and aux values are domain-separated by protocol, round, party set, curve, and subproof was already enforced and therefore lets replayed commitments, proofs, or messages are accepted in another execution.
- Invariant to test: The MPC transport path must preserve curve, key/blob version, party identity, session or label context, access-structure semantics, and validated encodings from `coinbase::api::ecdsa_2p::sign` through `include/cbmpc/core/job.h`.
- Expected Immunefi impact: Coinbase cb-mpc bounty (HackerOne, not Immunefi): High accepted cryptographic output bound to the wrong curve, key, label, session, party set, or protocol version.
- Fast validation: Write a local public-API harness with one honest unmodified party and malicious fake transport or buffers; mutate a reused sid, aux value, or transcript fragment from a concurrent execution; assert rejection before `include/cbmpc/core/job.h` `send` can produce a valid-looking signature, key blob, proof, ciphertext, plaintext, public share, or recovered scalar.
