# Q3554: ECDSA-2PC error-state confusion in ecdsa2pc.cpp

## Question
Can an unprivileged attacker enter through `coinbase::api::ecdsa_2p::attach_private_scalar` with public_key_blob, variable-length private_scalar, and public_share_compressed while two sessions run concurrently, reach `src/cbmpc/api/ecdsa2pc.cpp` `refresh`, and use input that triggers an inner parse/proof failure after partially filling output buffers to bypass the requirement that outputs are cleared or invalidated on every internal error path, causing a caller receives reusable partial output after validation failure and producing an in-scope cb-mpc bounty impact?

## Target
- File/function: `src/cbmpc/api/ecdsa2pc.cpp::refresh`
- Entrypoint: `coinbase::api::ecdsa_2p::attach_private_scalar via include/cbmpc/api/ecdsa_2p.h`
- Attacker controls: public_key_blob, variable-length private_scalar, and public_share_compressed; specifically input that triggers an inner parse/proof failure after partially filling output buffers while two sessions run concurrently
- Exploit idea: Start from supported public API `coinbase::api::ecdsa_2p::attach_private_scalar` in `include/cbmpc/api/ecdsa_2p.h` with public_key_blob, variable-length private_scalar, and public_share_compressed while two sessions run concurrently. The malicious side supplies input that triggers an inner parse/proof failure after partially filling output buffers. Investigate whether `src/cbmpc/api/ecdsa2pc.cpp` `refresh` assumes outputs are cleared or invalidated on every internal error path was already enforced and therefore lets a caller receives reusable partial output after validation failure.
- Invariant to test: The ECDSA-2PC path must preserve curve, key/blob version, party identity, session or label context, access-structure semantics, and validated encodings from `coinbase::api::ecdsa_2p::attach_private_scalar` through `src/cbmpc/api/ecdsa2pc.cpp`.
- Expected Immunefi impact: Coinbase cb-mpc bounty (HackerOne, not Immunefi): High public-API reachable validation bypass in a supported high-level protocol.
- Fast validation: Write a local public-API harness with one honest unmodified party and malicious fake transport or buffers; mutate input that triggers an inner parse/proof failure after partially filling output buffers; assert rejection before `src/cbmpc/api/ecdsa2pc.cpp` `refresh` can produce a valid-looking signature, key blob, proof, ciphertext, plaintext, public share, or recovered scalar.
