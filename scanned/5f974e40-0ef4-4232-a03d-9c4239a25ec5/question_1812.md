# Q1812: cb-mpc protocol refresh old-new key mix in curve_util.h

## Question
Can an unprivileged attacker enter through `coinbase::api::tdh2::partial_decrypt` with private_share, ciphertext, and label during the first accepted protocol run, reach `src/cbmpc/api/curve_util.h` `curve_util module`, and use refresh transcript that mixes old public shares with new private shares to bypass the requirement that refresh preserves the public key while replacing only intended secret shares, causing refreshed blob signs under a key different from exported public key and producing an in-scope cb-mpc bounty impact?

## Target
- File/function: `src/cbmpc/api/curve_util.h::curve_util module`
- Entrypoint: `coinbase::api::tdh2::partial_decrypt via include/cbmpc/api/tdh2.h`
- Attacker controls: private_share, ciphertext, and label; specifically refresh transcript that mixes old public shares with new private shares during the first accepted protocol run
- Exploit idea: Start from supported public API `coinbase::api::tdh2::partial_decrypt` in `include/cbmpc/api/tdh2.h` with private_share, ciphertext, and label during the first accepted protocol run. The malicious side supplies refresh transcript that mixes old public shares with new private shares. Investigate whether `src/cbmpc/api/curve_util.h` `curve_util module` assumes refresh preserves the public key while replacing only intended secret shares was already enforced and therefore lets refreshed blob signs under a key different from exported public key.
- Invariant to test: The cb-mpc protocol path must preserve curve, key/blob version, party identity, session or label context, access-structure semantics, and validated encodings from `coinbase::api::tdh2::partial_decrypt` through `src/cbmpc/api/curve_util.h`.
- Expected Immunefi impact: Coinbase cb-mpc bounty (HackerOne, not Immunefi): High accepted cryptographic output bound to the wrong curve, key, label, session, party set, or protocol version.
- Fast validation: Write a local public-API harness with one honest unmodified party and malicious fake transport or buffers; mutate refresh transcript that mixes old public shares with new private shares; assert rejection before `src/cbmpc/api/curve_util.h` `curve_util module` can produce a valid-looking signature, key blob, proof, ciphertext, plaintext, public share, or recovered scalar.
