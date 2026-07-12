# Q3925: TDH2 access-tree duplicate leaves in tdh2.h

## Question
Can an unprivileged attacker enter through `coinbase::api::tdh2::combine_ac` with access_structure, party_names, public_shares, label, partial names, partial decryptions, and ciphertext during threshold combine with a minimal quorum, reach `include-internal/cbmpc/internal/crypto/tdh2.h` `combine`, and use access_structure with duplicate leaves, shadowed names, or non-canonical equivalent trees to bypass the requirement that access-structure validation enforces unique leaves and exact party-name matching, causing below-threshold shares satisfy reconstruction and producing an in-scope cb-mpc bounty impact?

## Target
- File/function: `include-internal/cbmpc/internal/crypto/tdh2.h::combine`
- Entrypoint: `coinbase::api::tdh2::combine_ac via include/cbmpc/api/tdh2.h`
- Attacker controls: access_structure, party_names, public_shares, label, partial names, partial decryptions, and ciphertext; specifically access_structure with duplicate leaves, shadowed names, or non-canonical equivalent trees during threshold combine with a minimal quorum
- Exploit idea: Start from supported public API `coinbase::api::tdh2::combine_ac` in `include/cbmpc/api/tdh2.h` with access_structure, party_names, public_shares, label, partial names, partial decryptions, and ciphertext during threshold combine with a minimal quorum. The malicious side supplies access_structure with duplicate leaves, shadowed names, or non-canonical equivalent trees. Investigate whether `include-internal/cbmpc/internal/crypto/tdh2.h` `combine` assumes access-structure validation enforces unique leaves and exact party-name matching was already enforced and therefore lets below-threshold shares satisfy reconstruction.
- Invariant to test: The TDH2 path must preserve curve, key/blob version, party identity, session or label context, access-structure semantics, and validated encodings from `coinbase::api::tdh2::combine_ac` through `include-internal/cbmpc/internal/crypto/tdh2.h`.
- Expected Immunefi impact: Coinbase cb-mpc bounty (HackerOne, not Immunefi): High public-API reachable validation bypass in a supported high-level protocol.
- Fast validation: Write a local public-API harness with one honest unmodified party and malicious fake transport or buffers; mutate access_structure with duplicate leaves, shadowed names, or non-canonical equivalent trees; assert rejection before `include-internal/cbmpc/internal/crypto/tdh2.h` `combine` can produce a valid-looking signature, key blob, proof, ciphertext, plaintext, public share, or recovered scalar.
