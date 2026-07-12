# Q3956: ECDSA-2PC converter trailing-data trust in ecdsa2pc.cpp

## Question
Can an unprivileged attacker enter through `coinbase::api::ecdsa_2p::attach_private_scalar` with public_key_blob, variable-length private_scalar, and public_share_compressed during the first accepted protocol run, reach `src/cbmpc/api/ecdsa2pc.cpp` `get_public_key_compressed`, and use serialized object with a valid prefix plus trailing attacker fields to bypass the requirement that deserializers consume the full buffer and reject trailing or missing fields, causing displayed fields differ from internal parsed fields and producing an in-scope cb-mpc bounty impact?

## Target
- File/function: `src/cbmpc/api/ecdsa2pc.cpp::get_public_key_compressed`
- Entrypoint: `coinbase::api::ecdsa_2p::attach_private_scalar via include/cbmpc/api/ecdsa_2p.h`
- Attacker controls: public_key_blob, variable-length private_scalar, and public_share_compressed; specifically serialized object with a valid prefix plus trailing attacker fields during the first accepted protocol run
- Exploit idea: Start from supported public API `coinbase::api::ecdsa_2p::attach_private_scalar` in `include/cbmpc/api/ecdsa_2p.h` with public_key_blob, variable-length private_scalar, and public_share_compressed during the first accepted protocol run. The malicious side supplies serialized object with a valid prefix plus trailing attacker fields. Investigate whether `src/cbmpc/api/ecdsa2pc.cpp` `get_public_key_compressed` assumes deserializers consume the full buffer and reject trailing or missing fields was already enforced and therefore lets displayed fields differ from internal parsed fields.
- Invariant to test: The ECDSA-2PC path must preserve curve, key/blob version, party identity, session or label context, access-structure semantics, and validated encodings from `coinbase::api::ecdsa_2p::attach_private_scalar` through `src/cbmpc/api/ecdsa2pc.cpp`.
- Expected Immunefi impact: Coinbase cb-mpc bounty (HackerOne, not Immunefi): High public-API reachable validation bypass in a supported high-level protocol.
- Fast validation: Write a local public-API harness with one honest unmodified party and malicious fake transport or buffers; mutate serialized object with a valid prefix plus trailing attacker fields; assert rejection before `src/cbmpc/api/ecdsa2pc.cpp` `get_public_key_compressed` can produce a valid-looking signature, key blob, proof, ciphertext, plaintext, public share, or recovered scalar.
