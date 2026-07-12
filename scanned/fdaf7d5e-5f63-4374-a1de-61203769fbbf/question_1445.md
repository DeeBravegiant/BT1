# Q1445: ECC validation error-state confusion in ec25519_core.cpp

## Question
Can an unprivileged attacker enter through `coinbase::api::hd_keyset_ecdsa_2p::derive_ecdsa_2p_keys` with keyset_blob, hardened_path, and malicious derivation transcript after refresh but before public-key export, reach `src/cbmpc/crypto/ec25519_core.cpp` `from_bin`, and use input that triggers an inner parse/proof failure after partially filling output buffers to bypass the requirement that outputs are cleared or invalidated on every internal error path, causing a caller receives reusable partial output after validation failure and producing an in-scope cb-mpc bounty impact?

## Target
- File/function: `src/cbmpc/crypto/ec25519_core.cpp::from_bin`
- Entrypoint: `coinbase::api::hd_keyset_ecdsa_2p::derive_ecdsa_2p_keys via include/cbmpc/api/hd_keyset_ecdsa_2p.h`
- Attacker controls: keyset_blob, hardened_path, and malicious derivation transcript; specifically input that triggers an inner parse/proof failure after partially filling output buffers after refresh but before public-key export
- Exploit idea: Start from supported public API `coinbase::api::hd_keyset_ecdsa_2p::derive_ecdsa_2p_keys` in `include/cbmpc/api/hd_keyset_ecdsa_2p.h` with keyset_blob, hardened_path, and malicious derivation transcript after refresh but before public-key export. The malicious side supplies input that triggers an inner parse/proof failure after partially filling output buffers. Investigate whether `src/cbmpc/crypto/ec25519_core.cpp` `from_bin` assumes outputs are cleared or invalidated on every internal error path was already enforced and therefore lets a caller receives reusable partial output after validation failure.
- Invariant to test: The ECC validation path must preserve curve, key/blob version, party identity, session or label context, access-structure semantics, and validated encodings from `coinbase::api::hd_keyset_ecdsa_2p::derive_ecdsa_2p_keys` through `src/cbmpc/crypto/ec25519_core.cpp`.
- Expected Immunefi impact: Coinbase cb-mpc bounty (HackerOne, not Immunefi): High public-API reachable validation bypass in a supported high-level protocol.
- Fast validation: Write a local public-API harness with one honest unmodified party and malicious fake transport or buffers; mutate input that triggers an inner parse/proof failure after partially filling output buffers; assert rejection before `src/cbmpc/crypto/ec25519_core.cpp` `from_bin` can produce a valid-looking signature, key blob, proof, ciphertext, plaintext, public share, or recovered scalar.
