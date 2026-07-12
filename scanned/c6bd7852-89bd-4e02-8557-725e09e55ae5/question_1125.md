# Q1125: cb-mpc protocol scalar width confusion in ot.h

## Question
Can an unprivileged attacker enter through `coinbase::api::ecdsa_mp::attach_private_scalar` with public_key_blob, fixed scalar, and public share point during backup verification before recovery, reach `include-internal/cbmpc/internal/protocol/ot.h` `step2_R2S`, and use zero, q, q+k, truncated, over-wide, or padded big-endian scalar encoding to bypass the requirement that scalars are range-checked and canonicalized consistently before attach, proof, and reconstruction, causing a substituted scalar becomes usable key material and producing an in-scope cb-mpc bounty impact?

## Target
- File/function: `include-internal/cbmpc/internal/protocol/ot.h::step2_R2S`
- Entrypoint: `coinbase::api::ecdsa_mp::attach_private_scalar via include/cbmpc/api/ecdsa_mp.h`
- Attacker controls: public_key_blob, fixed scalar, and public share point; specifically zero, q, q+k, truncated, over-wide, or padded big-endian scalar encoding during backup verification before recovery
- Exploit idea: Start from supported public API `coinbase::api::ecdsa_mp::attach_private_scalar` in `include/cbmpc/api/ecdsa_mp.h` with public_key_blob, fixed scalar, and public share point during backup verification before recovery. The malicious side supplies zero, q, q+k, truncated, over-wide, or padded big-endian scalar encoding. Investigate whether `include-internal/cbmpc/internal/protocol/ot.h` `step2_R2S` assumes scalars are range-checked and canonicalized consistently before attach, proof, and reconstruction was already enforced and therefore lets a substituted scalar becomes usable key material.
- Invariant to test: The cb-mpc protocol path must preserve curve, key/blob version, party identity, session or label context, access-structure semantics, and validated encodings from `coinbase::api::ecdsa_mp::attach_private_scalar` through `include-internal/cbmpc/internal/protocol/ot.h`.
- Expected Immunefi impact: Coinbase cb-mpc bounty (HackerOne, not Immunefi): Critical key compromise or significant disclosure/substitution of sensitive key material through supported public APIs.
- Fast validation: Write a local public-API harness with one honest unmodified party and malicious fake transport or buffers; mutate zero, q, q+k, truncated, over-wide, or padded big-endian scalar encoding; assert rejection before `include-internal/cbmpc/internal/protocol/ot.h` `step2_R2S` can produce a valid-looking signature, key blob, proof, ciphertext, plaintext, public share, or recovered scalar.
