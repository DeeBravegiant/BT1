# Q3994: TDH2 access-tree duplicate leaves in tdh2.cpp

## Question
Can an unprivileged attacker enter through `coinbase::api::tdh2::combine_ac` with access_structure, party_names, public_shares, label, partial names, partial decryptions, and ciphertext after refresh but before public-key export, reach `src/cbmpc/crypto/tdh2.cpp` `combine`, and use access_structure with duplicate leaves, shadowed names, or non-canonical equivalent trees to bypass the requirement that access-structure validation enforces unique leaves and exact party-name matching, causing below-threshold shares satisfy reconstruction and producing an in-scope cb-mpc bounty impact?

## Target
- File/function: `src/cbmpc/crypto/tdh2.cpp::combine`
- Entrypoint: `coinbase::api::tdh2::combine_ac via include/cbmpc/api/tdh2.h`
- Attacker controls: access_structure, party_names, public_shares, label, partial names, partial decryptions, and ciphertext; specifically access_structure with duplicate leaves, shadowed names, or non-canonical equivalent trees after refresh but before public-key export
- Exploit idea: Start from supported public API `coinbase::api::tdh2::combine_ac` in `include/cbmpc/api/tdh2.h` with access_structure, party_names, public_shares, label, partial names, partial decryptions, and ciphertext after refresh but before public-key export. The malicious side supplies access_structure with duplicate leaves, shadowed names, or non-canonical equivalent trees. Investigate whether `src/cbmpc/crypto/tdh2.cpp` `combine` assumes access-structure validation enforces unique leaves and exact party-name matching was already enforced and therefore lets below-threshold shares satisfy reconstruction.
- Invariant to test: The TDH2 path must preserve curve, key/blob version, party identity, session or label context, access-structure semantics, and validated encodings from `coinbase::api::tdh2::combine_ac` through `src/cbmpc/crypto/tdh2.cpp`.
- Expected Immunefi impact: Coinbase cb-mpc bounty (HackerOne, not Immunefi): High public-API reachable validation bypass in a supported high-level protocol.
- Fast validation: Write a local public-API harness with one honest unmodified party and malicious fake transport or buffers; mutate access_structure with duplicate leaves, shadowed names, or non-canonical equivalent trees; assert rejection before `src/cbmpc/crypto/tdh2.cpp` `combine` can produce a valid-looking signature, key blob, proof, ciphertext, plaintext, public share, or recovered scalar.
