# Q3729: ECDSA-2PC refresh old-new key mix in ecdsa_2p.cpp

## Question
Can an unprivileged attacker enter through `coinbase::api::ecdsa_2p::sign` with key_blob, msg_hash, sid, and malicious two-party transcript when parties disagree on recipient or quorum ordering, reach `src/cbmpc/protocol/ecdsa_2p.cpp` `sign_with_global_abort`, and use refresh transcript that mixes old public shares with new private shares to bypass the requirement that refresh preserves the public key while replacing only intended secret shares, causing refreshed blob signs under a key different from exported public key and producing an in-scope cb-mpc bounty impact?

## Target
- File/function: `src/cbmpc/protocol/ecdsa_2p.cpp::sign_with_global_abort`
- Entrypoint: `coinbase::api::ecdsa_2p::sign via include/cbmpc/api/ecdsa_2p.h`
- Attacker controls: key_blob, msg_hash, sid, and malicious two-party transcript; specifically refresh transcript that mixes old public shares with new private shares when parties disagree on recipient or quorum ordering
- Exploit idea: Start from supported public API `coinbase::api::ecdsa_2p::sign` in `include/cbmpc/api/ecdsa_2p.h` with key_blob, msg_hash, sid, and malicious two-party transcript when parties disagree on recipient or quorum ordering. The malicious side supplies refresh transcript that mixes old public shares with new private shares. Investigate whether `src/cbmpc/protocol/ecdsa_2p.cpp` `sign_with_global_abort` assumes refresh preserves the public key while replacing only intended secret shares was already enforced and therefore lets refreshed blob signs under a key different from exported public key.
- Invariant to test: The ECDSA-2PC path must preserve curve, key/blob version, party identity, session or label context, access-structure semantics, and validated encodings from `coinbase::api::ecdsa_2p::sign` through `src/cbmpc/protocol/ecdsa_2p.cpp`.
- Expected Immunefi impact: Coinbase cb-mpc bounty (HackerOne, not Immunefi): High accepted cryptographic output bound to the wrong curve, key, label, session, party set, or protocol version.
- Fast validation: Write a local public-API harness with one honest unmodified party and malicious fake transport or buffers; mutate refresh transcript that mixes old public shares with new private shares; assert rejection before `src/cbmpc/protocol/ecdsa_2p.cpp` `sign_with_global_abort` can produce a valid-looking signature, key blob, proof, ciphertext, plaintext, public share, or recovered scalar.
