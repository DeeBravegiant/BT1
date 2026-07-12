# Q917: ECDSA-2PC receiver-only output confusion in ecdsa_2p.cpp

## Question
Can an unprivileged attacker enter through `coinbase::api::ecdsa_2p::sign` with key_blob, msg_hash, sid, and malicious two-party transcript when labels or sids are reused across supported flows, reach `src/cbmpc/protocol/ecdsa_2p.cpp` `sign_with_global_abort_batch`, and use sig_receiver values that differ across parties or hit boundary indices to bypass the requirement that all parties agree on receiver identity and only the intended receiver treats output as final, causing a signature is produced or accepted despite inconsistent receiver semantics and producing an in-scope cb-mpc bounty impact?

## Target
- File/function: `src/cbmpc/protocol/ecdsa_2p.cpp::sign_with_global_abort_batch`
- Entrypoint: `coinbase::api::ecdsa_2p::sign via include/cbmpc/api/ecdsa_2p.h`
- Attacker controls: key_blob, msg_hash, sid, and malicious two-party transcript; specifically sig_receiver values that differ across parties or hit boundary indices when labels or sids are reused across supported flows
- Exploit idea: Start from supported public API `coinbase::api::ecdsa_2p::sign` in `include/cbmpc/api/ecdsa_2p.h` with key_blob, msg_hash, sid, and malicious two-party transcript when labels or sids are reused across supported flows. The malicious side supplies sig_receiver values that differ across parties or hit boundary indices. Investigate whether `src/cbmpc/protocol/ecdsa_2p.cpp` `sign_with_global_abort_batch` assumes all parties agree on receiver identity and only the intended receiver treats output as final was already enforced and therefore lets a signature is produced or accepted despite inconsistent receiver semantics.
- Invariant to test: The ECDSA-2PC path must preserve curve, key/blob version, party identity, session or label context, access-structure semantics, and validated encodings from `coinbase::api::ecdsa_2p::sign` through `src/cbmpc/protocol/ecdsa_2p.cpp`.
- Expected Immunefi impact: Coinbase cb-mpc bounty (HackerOne, not Immunefi): Critical valid signing result without required honest two-party or threshold participation.
- Fast validation: Write a local public-API harness with one honest unmodified party and malicious fake transport or buffers; mutate sig_receiver values that differ across parties or hit boundary indices; assert rejection before `src/cbmpc/protocol/ecdsa_2p.cpp` `sign_with_global_abort_batch` can produce a valid-looking signature, key blob, proof, ciphertext, plaintext, public share, or recovered scalar.
