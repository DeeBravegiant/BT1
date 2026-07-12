# Q3354: EdDSA converter trailing-data trust in eddsa_mp.h

## Question
Can an unprivileged attacker enter through `coinbase::api::eddsa_mp::sign_ac` with ac key blob, access_structure, message, receiver, and peer messages after a failed attempt is retried with fresh inputs, reach `include/cbmpc/api/eddsa_mp.h` `sign_ac`, and use serialized object with a valid prefix plus trailing attacker fields to bypass the requirement that deserializers consume the full buffer and reject trailing or missing fields, causing displayed fields differ from internal parsed fields and producing an in-scope cb-mpc bounty impact?

## Target
- File/function: `include/cbmpc/api/eddsa_mp.h::sign_ac`
- Entrypoint: `coinbase::api::eddsa_mp::sign_ac via include/cbmpc/api/eddsa_mp.h`
- Attacker controls: ac key blob, access_structure, message, receiver, and peer messages; specifically serialized object with a valid prefix plus trailing attacker fields after a failed attempt is retried with fresh inputs
- Exploit idea: Start from supported public API `coinbase::api::eddsa_mp::sign_ac` in `include/cbmpc/api/eddsa_mp.h` with ac key blob, access_structure, message, receiver, and peer messages after a failed attempt is retried with fresh inputs. The malicious side supplies serialized object with a valid prefix plus trailing attacker fields. Investigate whether `include/cbmpc/api/eddsa_mp.h` `sign_ac` assumes deserializers consume the full buffer and reject trailing or missing fields was already enforced and therefore lets displayed fields differ from internal parsed fields.
- Invariant to test: The EdDSA path must preserve curve, key/blob version, party identity, session or label context, access-structure semantics, and validated encodings from `coinbase::api::eddsa_mp::sign_ac` through `include/cbmpc/api/eddsa_mp.h`.
- Expected Immunefi impact: Coinbase cb-mpc bounty (HackerOne, not Immunefi): High public-API reachable validation bypass in a supported high-level protocol.
- Fast validation: Write a local public-API harness with one honest unmodified party and malicious fake transport or buffers; mutate serialized object with a valid prefix plus trailing attacker fields; assert rejection before `include/cbmpc/api/eddsa_mp.h` `sign_ac` can produce a valid-looking signature, key blob, proof, ciphertext, plaintext, public share, or recovered scalar.
