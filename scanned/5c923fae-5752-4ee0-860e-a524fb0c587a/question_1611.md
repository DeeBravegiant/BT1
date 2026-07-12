# Q1611: ECDSA-MP access-tree duplicate leaves in ecdsa_mp.h

## Question
Can an unprivileged attacker enter through `coinbase::api::ecdsa_mp::sign_ac` with ac_key_blob, access_structure, msg, sig_receiver, and malicious peer messages after refresh but before public-key export, reach `include/cbmpc/api/ecdsa_mp.h` `refresh_additive`, and use access_structure with duplicate leaves, shadowed names, or non-canonical equivalent trees to bypass the requirement that access-structure validation enforces unique leaves and exact party-name matching, causing below-threshold shares satisfy reconstruction and producing an in-scope cb-mpc bounty impact?

## Target
- File/function: `include/cbmpc/api/ecdsa_mp.h::refresh_additive`
- Entrypoint: `coinbase::api::ecdsa_mp::sign_ac via include/cbmpc/api/ecdsa_mp.h`
- Attacker controls: ac_key_blob, access_structure, msg, sig_receiver, and malicious peer messages; specifically access_structure with duplicate leaves, shadowed names, or non-canonical equivalent trees after refresh but before public-key export
- Exploit idea: Start from supported public API `coinbase::api::ecdsa_mp::sign_ac` in `include/cbmpc/api/ecdsa_mp.h` with ac_key_blob, access_structure, msg, sig_receiver, and malicious peer messages after refresh but before public-key export. The malicious side supplies access_structure with duplicate leaves, shadowed names, or non-canonical equivalent trees. Investigate whether `include/cbmpc/api/ecdsa_mp.h` `refresh_additive` assumes access-structure validation enforces unique leaves and exact party-name matching was already enforced and therefore lets below-threshold shares satisfy reconstruction.
- Invariant to test: The ECDSA-MP path must preserve curve, key/blob version, party identity, session or label context, access-structure semantics, and validated encodings from `coinbase::api::ecdsa_mp::sign_ac` through `include/cbmpc/api/ecdsa_mp.h`.
- Expected Immunefi impact: Coinbase cb-mpc bounty (HackerOne, not Immunefi): High public-API reachable validation bypass in a supported high-level protocol.
- Fast validation: Write a local public-API harness with one honest unmodified party and malicious fake transport or buffers; mutate access_structure with duplicate leaves, shadowed names, or non-canonical equivalent trees; assert rejection before `include/cbmpc/api/ecdsa_mp.h` `refresh_additive` can produce a valid-looking signature, key blob, proof, ciphertext, plaintext, public share, or recovered scalar.
