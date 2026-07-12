# Q1487: TDH2 refresh old-new key mix in tdh2.h

## Question
Can an unprivileged attacker enter through `coinbase::api::tdh2::combine_ac` with access_structure, party_names, public_shares, label, partial names, partial decryptions, and ciphertext when the same caller alternates valid and mutated blobs, reach `include/cbmpc/api/tdh2.h` `partial_decrypt`, and use refresh transcript that mixes old public shares with new private shares to bypass the requirement that refresh preserves the public key while replacing only intended secret shares, causing refreshed blob signs under a key different from exported public key and producing an in-scope cb-mpc bounty impact?

## Target
- File/function: `include/cbmpc/api/tdh2.h::partial_decrypt`
- Entrypoint: `coinbase::api::tdh2::combine_ac via include/cbmpc/api/tdh2.h`
- Attacker controls: access_structure, party_names, public_shares, label, partial names, partial decryptions, and ciphertext; specifically refresh transcript that mixes old public shares with new private shares when the same caller alternates valid and mutated blobs
- Exploit idea: Start from supported public API `coinbase::api::tdh2::combine_ac` in `include/cbmpc/api/tdh2.h` with access_structure, party_names, public_shares, label, partial names, partial decryptions, and ciphertext when the same caller alternates valid and mutated blobs. The malicious side supplies refresh transcript that mixes old public shares with new private shares. Investigate whether `include/cbmpc/api/tdh2.h` `partial_decrypt` assumes refresh preserves the public key while replacing only intended secret shares was already enforced and therefore lets refreshed blob signs under a key different from exported public key.
- Invariant to test: The TDH2 path must preserve curve, key/blob version, party identity, session or label context, access-structure semantics, and validated encodings from `coinbase::api::tdh2::combine_ac` through `include/cbmpc/api/tdh2.h`.
- Expected Immunefi impact: Coinbase cb-mpc bounty (HackerOne, not Immunefi): High accepted cryptographic output bound to the wrong curve, key, label, session, party set, or protocol version.
- Fast validation: Write a local public-API harness with one honest unmodified party and malicious fake transport or buffers; mutate refresh transcript that mixes old public shares with new private shares; assert rejection before `include/cbmpc/api/tdh2.h` `partial_decrypt` can produce a valid-looking signature, key blob, proof, ciphertext, plaintext, public share, or recovered scalar.
