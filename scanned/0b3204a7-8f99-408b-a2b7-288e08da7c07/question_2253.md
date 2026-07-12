# Q2253: TDH2 public key extraction mismatch in tdh2.cpp

## Question
Can an unprivileged attacker enter through `coinbase::api::tdh2::partial_decrypt` with private_share, ciphertext, and label during the first accepted protocol run, reach `src/cbmpc/crypto/tdh2.cpp` `decrypt`, and use key_blob whose public-key extraction path and signing path parse different fields to bypass the requirement that exported public key is derived from the same validated state used by signing, causing caller authorizes one public key while protocol signs with another and producing an in-scope cb-mpc bounty impact?

## Target
- File/function: `src/cbmpc/crypto/tdh2.cpp::decrypt`
- Entrypoint: `coinbase::api::tdh2::partial_decrypt via include/cbmpc/api/tdh2.h`
- Attacker controls: private_share, ciphertext, and label; specifically key_blob whose public-key extraction path and signing path parse different fields during the first accepted protocol run
- Exploit idea: Start from supported public API `coinbase::api::tdh2::partial_decrypt` in `include/cbmpc/api/tdh2.h` with private_share, ciphertext, and label during the first accepted protocol run. The malicious side supplies key_blob whose public-key extraction path and signing path parse different fields. Investigate whether `src/cbmpc/crypto/tdh2.cpp` `decrypt` assumes exported public key is derived from the same validated state used by signing was already enforced and therefore lets caller authorizes one public key while protocol signs with another.
- Invariant to test: The TDH2 path must preserve curve, key/blob version, party identity, session or label context, access-structure semantics, and validated encodings from `coinbase::api::tdh2::partial_decrypt` through `src/cbmpc/crypto/tdh2.cpp`.
- Expected Immunefi impact: Coinbase cb-mpc bounty (HackerOne, not Immunefi): High accepted cryptographic output bound to the wrong curve, key, label, session, party set, or protocol version.
- Fast validation: Write a local public-API harness with one honest unmodified party and malicious fake transport or buffers; mutate key_blob whose public-key extraction path and signing path parse different fields; assert rejection before `src/cbmpc/crypto/tdh2.cpp` `decrypt` can produce a valid-looking signature, key blob, proof, ciphertext, plaintext, public share, or recovered scalar.
