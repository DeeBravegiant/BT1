# Q2586: TDH2 session replay in tdh2.h

## Question
Can an unprivileged attacker enter through `coinbase::api::tdh2::verify` with public_key, ciphertext, and label during the first accepted protocol run, reach `include-internal/cbmpc/internal/crypto/tdh2.h` `verify`, and use a reused sid, aux value, or transcript fragment from a concurrent execution to bypass the requirement that session and aux values are domain-separated by protocol, round, party set, curve, and subproof, causing replayed commitments, proofs, or messages are accepted in another execution and producing an in-scope cb-mpc bounty impact?

## Target
- File/function: `include-internal/cbmpc/internal/crypto/tdh2.h::verify`
- Entrypoint: `coinbase::api::tdh2::verify via include/cbmpc/api/tdh2.h`
- Attacker controls: public_key, ciphertext, and label; specifically a reused sid, aux value, or transcript fragment from a concurrent execution during the first accepted protocol run
- Exploit idea: Start from supported public API `coinbase::api::tdh2::verify` in `include/cbmpc/api/tdh2.h` with public_key, ciphertext, and label during the first accepted protocol run. The malicious side supplies a reused sid, aux value, or transcript fragment from a concurrent execution. Investigate whether `include-internal/cbmpc/internal/crypto/tdh2.h` `verify` assumes session and aux values are domain-separated by protocol, round, party set, curve, and subproof was already enforced and therefore lets replayed commitments, proofs, or messages are accepted in another execution.
- Invariant to test: The TDH2 path must preserve curve, key/blob version, party identity, session or label context, access-structure semantics, and validated encodings from `coinbase::api::tdh2::verify` through `include-internal/cbmpc/internal/crypto/tdh2.h`.
- Expected Immunefi impact: Coinbase cb-mpc bounty (HackerOne, not Immunefi): High accepted cryptographic output bound to the wrong curve, key, label, session, party set, or protocol version.
- Fast validation: Write a local public-API harness with one honest unmodified party and malicious fake transport or buffers; mutate a reused sid, aux value, or transcript fragment from a concurrent execution; assert rejection before `include-internal/cbmpc/internal/crypto/tdh2.h` `verify` can produce a valid-looking signature, key blob, proof, ciphertext, plaintext, public share, or recovered scalar.
