# Q670: ZK proof scalar width confusion in zk_unknown_order.cpp

## Question
Can an unprivileged attacker enter through `coinbase::api::ecdsa_mp::refresh_ac` with ac key_blob, access_structure, quorum names, sid, and peer messages after successful DKG and before signing, reach `src/cbmpc/zk/zk_unknown_order.cpp` `verify`, and use zero, q, q+k, truncated, over-wide, or padded big-endian scalar encoding to bypass the requirement that scalars are range-checked and canonicalized consistently before attach, proof, and reconstruction, causing a substituted scalar becomes usable key material and producing an in-scope cb-mpc bounty impact?

## Target
- File/function: `src/cbmpc/zk/zk_unknown_order.cpp::verify`
- Entrypoint: `coinbase::api::ecdsa_mp::refresh_ac via include/cbmpc/api/ecdsa_mp.h`
- Attacker controls: ac key_blob, access_structure, quorum names, sid, and peer messages; specifically zero, q, q+k, truncated, over-wide, or padded big-endian scalar encoding after successful DKG and before signing
- Exploit idea: Start from supported public API `coinbase::api::ecdsa_mp::refresh_ac` in `include/cbmpc/api/ecdsa_mp.h` with ac key_blob, access_structure, quorum names, sid, and peer messages after successful DKG and before signing. The malicious side supplies zero, q, q+k, truncated, over-wide, or padded big-endian scalar encoding. Investigate whether `src/cbmpc/zk/zk_unknown_order.cpp` `verify` assumes scalars are range-checked and canonicalized consistently before attach, proof, and reconstruction was already enforced and therefore lets a substituted scalar becomes usable key material.
- Invariant to test: The ZK proof path must preserve curve, key/blob version, party identity, session or label context, access-structure semantics, and validated encodings from `coinbase::api::ecdsa_mp::refresh_ac` through `src/cbmpc/zk/zk_unknown_order.cpp`.
- Expected Immunefi impact: Coinbase cb-mpc bounty (HackerOne, not Immunefi): Critical key compromise or significant disclosure/substitution of sensitive key material through supported public APIs.
- Fast validation: Write a local public-API harness with one honest unmodified party and malicious fake transport or buffers; mutate zero, q, q+k, truncated, over-wide, or padded big-endian scalar encoding; assert rejection before `src/cbmpc/zk/zk_unknown_order.cpp` `verify` can produce a valid-looking signature, key blob, proof, ciphertext, plaintext, public share, or recovered scalar.
