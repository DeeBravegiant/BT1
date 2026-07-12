# Q3848: ECC validation converter trailing-data trust in base_ecc_secp256k1.cpp

## Question
Can an unprivileged attacker enter through `coinbase::api::schnorr_mp::sign_ac` with ac key blob, access_structure, digest, receiver, and peer messages when parties disagree on recipient or quorum ordering, reach `src/cbmpc/crypto/base_ecc_secp256k1.cpp` `pub_from_der`, and use serialized object with a valid prefix plus trailing attacker fields to bypass the requirement that deserializers consume the full buffer and reject trailing or missing fields, causing displayed fields differ from internal parsed fields and producing an in-scope cb-mpc bounty impact?

## Target
- File/function: `src/cbmpc/crypto/base_ecc_secp256k1.cpp::pub_from_der`
- Entrypoint: `coinbase::api::schnorr_mp::sign_ac via include/cbmpc/api/schnorr_mp.h`
- Attacker controls: ac key blob, access_structure, digest, receiver, and peer messages; specifically serialized object with a valid prefix plus trailing attacker fields when parties disagree on recipient or quorum ordering
- Exploit idea: Start from supported public API `coinbase::api::schnorr_mp::sign_ac` in `include/cbmpc/api/schnorr_mp.h` with ac key blob, access_structure, digest, receiver, and peer messages when parties disagree on recipient or quorum ordering. The malicious side supplies serialized object with a valid prefix plus trailing attacker fields. Investigate whether `src/cbmpc/crypto/base_ecc_secp256k1.cpp` `pub_from_der` assumes deserializers consume the full buffer and reject trailing or missing fields was already enforced and therefore lets displayed fields differ from internal parsed fields.
- Invariant to test: The ECC validation path must preserve curve, key/blob version, party identity, session or label context, access-structure semantics, and validated encodings from `coinbase::api::schnorr_mp::sign_ac` through `src/cbmpc/crypto/base_ecc_secp256k1.cpp`.
- Expected Immunefi impact: Coinbase cb-mpc bounty (HackerOne, not Immunefi): High public-API reachable validation bypass in a supported high-level protocol.
- Fast validation: Write a local public-API harness with one honest unmodified party and malicious fake transport or buffers; mutate serialized object with a valid prefix plus trailing attacker fields; assert rejection before `src/cbmpc/crypto/base_ecc_secp256k1.cpp` `pub_from_der` can produce a valid-looking signature, key blob, proof, ciphertext, plaintext, public share, or recovered scalar.
