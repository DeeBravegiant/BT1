# Q1763: cb-mpc protocol scalar width confusion in base_bn.h

## Question
Can an unprivileged attacker enter through `coinbase::api::tdh2::partial_decrypt` with private_share, ciphertext, and label when parties disagree on recipient or quorum ordering, reach `include-internal/cbmpc/internal/crypto/base_bn.h` `from_string`, and use zero, q, q+k, truncated, over-wide, or padded big-endian scalar encoding to bypass the requirement that scalars are range-checked and canonicalized consistently before attach, proof, and reconstruction, causing a substituted scalar becomes usable key material and producing an in-scope cb-mpc bounty impact?

## Target
- File/function: `include-internal/cbmpc/internal/crypto/base_bn.h::from_string`
- Entrypoint: `coinbase::api::tdh2::partial_decrypt via include/cbmpc/api/tdh2.h`
- Attacker controls: private_share, ciphertext, and label; specifically zero, q, q+k, truncated, over-wide, or padded big-endian scalar encoding when parties disagree on recipient or quorum ordering
- Exploit idea: Start from supported public API `coinbase::api::tdh2::partial_decrypt` in `include/cbmpc/api/tdh2.h` with private_share, ciphertext, and label when parties disagree on recipient or quorum ordering. The malicious side supplies zero, q, q+k, truncated, over-wide, or padded big-endian scalar encoding. Investigate whether `include-internal/cbmpc/internal/crypto/base_bn.h` `from_string` assumes scalars are range-checked and canonicalized consistently before attach, proof, and reconstruction was already enforced and therefore lets a substituted scalar becomes usable key material.
- Invariant to test: The cb-mpc protocol path must preserve curve, key/blob version, party identity, session or label context, access-structure semantics, and validated encodings from `coinbase::api::tdh2::partial_decrypt` through `include-internal/cbmpc/internal/crypto/base_bn.h`.
- Expected Immunefi impact: Coinbase cb-mpc bounty (HackerOne, not Immunefi): Critical key compromise or significant disclosure/substitution of sensitive key material through supported public APIs.
- Fast validation: Write a local public-API harness with one honest unmodified party and malicious fake transport or buffers; mutate zero, q, q+k, truncated, over-wide, or padded big-endian scalar encoding; assert rejection before `include-internal/cbmpc/internal/crypto/base_bn.h` `from_string` can produce a valid-looking signature, key blob, proof, ciphertext, plaintext, public share, or recovered scalar.
