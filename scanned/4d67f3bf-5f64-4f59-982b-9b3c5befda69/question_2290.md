# Q2290: TDH2 public key extraction mismatch in tdh2.h

## Question
Can an unprivileged attacker enter through `coinbase::api::tdh2::verify` with public_key, ciphertext, and label after refresh but before public-key export, reach `include/cbmpc/api/tdh2.h` `verify`, and use key_blob whose public-key extraction path and signing path parse different fields to bypass the requirement that exported public key is derived from the same validated state used by signing, causing caller authorizes one public key while protocol signs with another and producing an in-scope cb-mpc bounty impact?

## Target
- File/function: `include/cbmpc/api/tdh2.h::verify`
- Entrypoint: `coinbase::api::tdh2::verify via include/cbmpc/api/tdh2.h`
- Attacker controls: public_key, ciphertext, and label; specifically key_blob whose public-key extraction path and signing path parse different fields after refresh but before public-key export
- Exploit idea: Start from supported public API `coinbase::api::tdh2::verify` in `include/cbmpc/api/tdh2.h` with public_key, ciphertext, and label after refresh but before public-key export. The malicious side supplies key_blob whose public-key extraction path and signing path parse different fields. Investigate whether `include/cbmpc/api/tdh2.h` `verify` assumes exported public key is derived from the same validated state used by signing was already enforced and therefore lets caller authorizes one public key while protocol signs with another.
- Invariant to test: The TDH2 path must preserve curve, key/blob version, party identity, session or label context, access-structure semantics, and validated encodings from `coinbase::api::tdh2::verify` through `include/cbmpc/api/tdh2.h`.
- Expected Immunefi impact: Coinbase cb-mpc bounty (HackerOne, not Immunefi): High accepted cryptographic output bound to the wrong curve, key, label, session, party set, or protocol version.
- Fast validation: Write a local public-API harness with one honest unmodified party and malicious fake transport or buffers; mutate key_blob whose public-key extraction path and signing path parse different fields; assert rejection before `include/cbmpc/api/tdh2.h` `verify` can produce a valid-looking signature, key blob, proof, ciphertext, plaintext, public share, or recovered scalar.
