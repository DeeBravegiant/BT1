# Q2992: ECDSA-2PC proof flag trust gap in ecdsa_2p.h

## Question
Can an unprivileged attacker enter through `coinbase::api::ecdsa_2p::sign` with key_blob, msg_hash, sid, and malicious two-party transcript after successful DKG and before signing, reach `include-internal/cbmpc/internal/protocol/ecdsa_2p.h` `sign_with_global_abort`, and use proof messages that imply prerequisite proof flags without the prerequisite transcript to bypass the requirement that every prerequisite ZK statement is established before dependent flags are trusted, causing an invalid cryptographic statement feeds an accepted protocol output and producing an in-scope cb-mpc bounty impact?

## Target
- File/function: `include-internal/cbmpc/internal/protocol/ecdsa_2p.h::sign_with_global_abort`
- Entrypoint: `coinbase::api::ecdsa_2p::sign via include/cbmpc/api/ecdsa_2p.h`
- Attacker controls: key_blob, msg_hash, sid, and malicious two-party transcript; specifically proof messages that imply prerequisite proof flags without the prerequisite transcript after successful DKG and before signing
- Exploit idea: Start from supported public API `coinbase::api::ecdsa_2p::sign` in `include/cbmpc/api/ecdsa_2p.h` with key_blob, msg_hash, sid, and malicious two-party transcript after successful DKG and before signing. The malicious side supplies proof messages that imply prerequisite proof flags without the prerequisite transcript. Investigate whether `include-internal/cbmpc/internal/protocol/ecdsa_2p.h` `sign_with_global_abort` assumes every prerequisite ZK statement is established before dependent flags are trusted was already enforced and therefore lets an invalid cryptographic statement feeds an accepted protocol output.
- Invariant to test: The ECDSA-2PC path must preserve curve, key/blob version, party identity, session or label context, access-structure semantics, and validated encodings from `coinbase::api::ecdsa_2p::sign` through `include-internal/cbmpc/internal/protocol/ecdsa_2p.h`.
- Expected Immunefi impact: Coinbase cb-mpc bounty (HackerOne, not Immunefi): High public-API reachable validation bypass in a supported high-level protocol.
- Fast validation: Write a local public-API harness with one honest unmodified party and malicious fake transport or buffers; mutate proof messages that imply prerequisite proof flags without the prerequisite transcript; assert rejection before `include-internal/cbmpc/internal/protocol/ecdsa_2p.h` `sign_with_global_abort` can produce a valid-looking signature, key blob, proof, ciphertext, plaintext, public share, or recovered scalar.
