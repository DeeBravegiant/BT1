# Q1246: access-structure converter trailing-data trust in secret_sharing.h

## Question
Can an unprivileged attacker enter through `coinbase::api::ecdsa_mp::sign_ac` with ac_key_blob, access_structure, msg, sig_receiver, and malicious peer messages after a failed attempt is retried with fresh inputs, reach `include-internal/cbmpc/internal/crypto/secret_sharing.h` `reconstruct_exponent`, and use serialized object with a valid prefix plus trailing attacker fields to bypass the requirement that deserializers consume the full buffer and reject trailing or missing fields, causing displayed fields differ from internal parsed fields and producing an in-scope cb-mpc bounty impact?

## Target
- File/function: `include-internal/cbmpc/internal/crypto/secret_sharing.h::reconstruct_exponent`
- Entrypoint: `coinbase::api::ecdsa_mp::sign_ac via include/cbmpc/api/ecdsa_mp.h`
- Attacker controls: ac_key_blob, access_structure, msg, sig_receiver, and malicious peer messages; specifically serialized object with a valid prefix plus trailing attacker fields after a failed attempt is retried with fresh inputs
- Exploit idea: Start from supported public API `coinbase::api::ecdsa_mp::sign_ac` in `include/cbmpc/api/ecdsa_mp.h` with ac_key_blob, access_structure, msg, sig_receiver, and malicious peer messages after a failed attempt is retried with fresh inputs. The malicious side supplies serialized object with a valid prefix plus trailing attacker fields. Investigate whether `include-internal/cbmpc/internal/crypto/secret_sharing.h` `reconstruct_exponent` assumes deserializers consume the full buffer and reject trailing or missing fields was already enforced and therefore lets displayed fields differ from internal parsed fields.
- Invariant to test: The access-structure path must preserve curve, key/blob version, party identity, session or label context, access-structure semantics, and validated encodings from `coinbase::api::ecdsa_mp::sign_ac` through `include-internal/cbmpc/internal/crypto/secret_sharing.h`.
- Expected Immunefi impact: Coinbase cb-mpc bounty (HackerOne, not Immunefi): High public-API reachable validation bypass in a supported high-level protocol.
- Fast validation: Write a local public-API harness with one honest unmodified party and malicious fake transport or buffers; mutate serialized object with a valid prefix plus trailing attacker fields; assert rejection before `include-internal/cbmpc/internal/crypto/secret_sharing.h` `reconstruct_exponent` can produce a valid-looking signature, key blob, proof, ciphertext, plaintext, public share, or recovered scalar.
