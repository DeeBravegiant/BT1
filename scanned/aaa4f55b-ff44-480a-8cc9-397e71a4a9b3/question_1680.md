# Q1680: ECDSA-2PC public share substitution in ecdsa2pc.cpp

## Question
Can an unprivileged attacker enter through `coinbase::api::ecdsa_2p::sign` with key_blob, msg_hash, sid, and malicious two-party transcript when parties disagree on recipient or quorum ordering, reach `src/cbmpc/api/ecdsa2pc.cpp` `serialize_key_blob`, and use public_share_compressed from one blob paired with scalar from another blob to bypass the requirement that attach APIs bind scalar to blob role, curve, public share, public key, and party, causing attacker restores scalar into a blob that should not be sign-capable and producing an in-scope cb-mpc bounty impact?

## Target
- File/function: `src/cbmpc/api/ecdsa2pc.cpp::serialize_key_blob`
- Entrypoint: `coinbase::api::ecdsa_2p::sign via include/cbmpc/api/ecdsa_2p.h`
- Attacker controls: key_blob, msg_hash, sid, and malicious two-party transcript; specifically public_share_compressed from one blob paired with scalar from another blob when parties disagree on recipient or quorum ordering
- Exploit idea: Start from supported public API `coinbase::api::ecdsa_2p::sign` in `include/cbmpc/api/ecdsa_2p.h` with key_blob, msg_hash, sid, and malicious two-party transcript when parties disagree on recipient or quorum ordering. The malicious side supplies public_share_compressed from one blob paired with scalar from another blob. Investigate whether `src/cbmpc/api/ecdsa2pc.cpp` `serialize_key_blob` assumes attach APIs bind scalar to blob role, curve, public share, public key, and party was already enforced and therefore lets attacker restores scalar into a blob that should not be sign-capable.
- Invariant to test: The ECDSA-2PC path must preserve curve, key/blob version, party identity, session or label context, access-structure semantics, and validated encodings from `coinbase::api::ecdsa_2p::sign` through `src/cbmpc/api/ecdsa2pc.cpp`.
- Expected Immunefi impact: Coinbase cb-mpc bounty (HackerOne, not Immunefi): Critical key compromise or significant disclosure/substitution of sensitive key material through supported public APIs.
- Fast validation: Write a local public-API harness with one honest unmodified party and malicious fake transport or buffers; mutate public_share_compressed from one blob paired with scalar from another blob; assert rejection before `src/cbmpc/api/ecdsa2pc.cpp` `serialize_key_blob` can produce a valid-looking signature, key blob, proof, ciphertext, plaintext, public share, or recovered scalar.
