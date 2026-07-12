# Q1450: TDH2 refresh old-new key mix in tdh2.cpp

## Question
Can an unprivileged attacker enter through `coinbase::api::tdh2::verify` with public_key, ciphertext, and label when public extraction is compared with signing output, reach `src/cbmpc/crypto/tdh2.cpp` `verify`, and use refresh transcript that mixes old public shares with new private shares to bypass the requirement that refresh preserves the public key while replacing only intended secret shares, causing refreshed blob signs under a key different from exported public key and producing an in-scope cb-mpc bounty impact?

## Target
- File/function: `src/cbmpc/crypto/tdh2.cpp::verify`
- Entrypoint: `coinbase::api::tdh2::verify via include/cbmpc/api/tdh2.h`
- Attacker controls: public_key, ciphertext, and label; specifically refresh transcript that mixes old public shares with new private shares when public extraction is compared with signing output
- Exploit idea: Start from supported public API `coinbase::api::tdh2::verify` in `include/cbmpc/api/tdh2.h` with public_key, ciphertext, and label when public extraction is compared with signing output. The malicious side supplies refresh transcript that mixes old public shares with new private shares. Investigate whether `src/cbmpc/crypto/tdh2.cpp` `verify` assumes refresh preserves the public key while replacing only intended secret shares was already enforced and therefore lets refreshed blob signs under a key different from exported public key.
- Invariant to test: The TDH2 path must preserve curve, key/blob version, party identity, session or label context, access-structure semantics, and validated encodings from `coinbase::api::tdh2::verify` through `src/cbmpc/crypto/tdh2.cpp`.
- Expected Immunefi impact: Coinbase cb-mpc bounty (HackerOne, not Immunefi): High accepted cryptographic output bound to the wrong curve, key, label, session, party set, or protocol version.
- Fast validation: Write a local public-API harness with one honest unmodified party and malicious fake transport or buffers; mutate refresh transcript that mixes old public shares with new private shares; assert rejection before `src/cbmpc/crypto/tdh2.cpp` `verify` can produce a valid-looking signature, key blob, proof, ciphertext, plaintext, public share, or recovered scalar.
