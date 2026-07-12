# Q759: serialization/core converter trailing-data trust in buf128.cpp

## Question
Can an unprivileged attacker enter through `coinbase::api::ecdsa_mp::attach_private_scalar` with public_key_blob, fixed scalar, and public share point when public extraction is compared with signing output, reach `src/cbmpc/core/buf128.cpp` `buf128 module`, and use serialized object with a valid prefix plus trailing attacker fields to bypass the requirement that deserializers consume the full buffer and reject trailing or missing fields, causing displayed fields differ from internal parsed fields and producing an in-scope cb-mpc bounty impact?

## Target
- File/function: `src/cbmpc/core/buf128.cpp::buf128 module`
- Entrypoint: `coinbase::api::ecdsa_mp::attach_private_scalar via include/cbmpc/api/ecdsa_mp.h`
- Attacker controls: public_key_blob, fixed scalar, and public share point; specifically serialized object with a valid prefix plus trailing attacker fields when public extraction is compared with signing output
- Exploit idea: Start from supported public API `coinbase::api::ecdsa_mp::attach_private_scalar` in `include/cbmpc/api/ecdsa_mp.h` with public_key_blob, fixed scalar, and public share point when public extraction is compared with signing output. The malicious side supplies serialized object with a valid prefix plus trailing attacker fields. Investigate whether `src/cbmpc/core/buf128.cpp` `buf128 module` assumes deserializers consume the full buffer and reject trailing or missing fields was already enforced and therefore lets displayed fields differ from internal parsed fields.
- Invariant to test: The serialization/core path must preserve curve, key/blob version, party identity, session or label context, access-structure semantics, and validated encodings from `coinbase::api::ecdsa_mp::attach_private_scalar` through `src/cbmpc/core/buf128.cpp`.
- Expected Immunefi impact: Coinbase cb-mpc bounty (HackerOne, not Immunefi): High public-API reachable validation bypass in a supported high-level protocol.
- Fast validation: Write a local public-API harness with one honest unmodified party and malicious fake transport or buffers; mutate serialized object with a valid prefix plus trailing attacker fields; assert rejection before `src/cbmpc/core/buf128.cpp` `buf128 module` can produce a valid-looking signature, key blob, proof, ciphertext, plaintext, public share, or recovered scalar.
