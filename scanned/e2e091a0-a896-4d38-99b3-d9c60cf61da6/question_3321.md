# Q3321: ZK proof converter trailing-data trust in elgamal.cpp

## Question
Can an unprivileged attacker enter through `coinbase::api::tdh2::verify` with public_key, ciphertext, and label while one malicious peer deviates and one honest party is unmodified, reach `src/cbmpc/crypto/elgamal.cpp` `elgamal module`, and use serialized object with a valid prefix plus trailing attacker fields to bypass the requirement that deserializers consume the full buffer and reject trailing or missing fields, causing displayed fields differ from internal parsed fields and producing an in-scope cb-mpc bounty impact?

## Target
- File/function: `src/cbmpc/crypto/elgamal.cpp::elgamal module`
- Entrypoint: `coinbase::api::tdh2::verify via include/cbmpc/api/tdh2.h`
- Attacker controls: public_key, ciphertext, and label; specifically serialized object with a valid prefix plus trailing attacker fields while one malicious peer deviates and one honest party is unmodified
- Exploit idea: Start from supported public API `coinbase::api::tdh2::verify` in `include/cbmpc/api/tdh2.h` with public_key, ciphertext, and label while one malicious peer deviates and one honest party is unmodified. The malicious side supplies serialized object with a valid prefix plus trailing attacker fields. Investigate whether `src/cbmpc/crypto/elgamal.cpp` `elgamal module` assumes deserializers consume the full buffer and reject trailing or missing fields was already enforced and therefore lets displayed fields differ from internal parsed fields.
- Invariant to test: The ZK proof path must preserve curve, key/blob version, party identity, session or label context, access-structure semantics, and validated encodings from `coinbase::api::tdh2::verify` through `src/cbmpc/crypto/elgamal.cpp`.
- Expected Immunefi impact: Coinbase cb-mpc bounty (HackerOne, not Immunefi): High public-API reachable validation bypass in a supported high-level protocol.
- Fast validation: Write a local public-API harness with one honest unmodified party and malicious fake transport or buffers; mutate serialized object with a valid prefix plus trailing attacker fields; assert rejection before `src/cbmpc/crypto/elgamal.cpp` `elgamal module` can produce a valid-looking signature, key blob, proof, ciphertext, plaintext, public share, or recovered scalar.
