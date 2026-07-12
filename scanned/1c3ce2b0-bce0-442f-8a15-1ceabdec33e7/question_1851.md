# Q1851: TDH2 public share substitution in tdh2.cpp

## Question
Can an unprivileged attacker enter through `coinbase::api::tdh2::combine_ac` with access_structure, party_names, public_shares, label, partial names, partial decryptions, and ciphertext while two sessions run concurrently, reach `src/cbmpc/crypto/tdh2.cpp` `combine_additive`, and use public_share_compressed from one blob paired with scalar from another blob to bypass the requirement that attach APIs bind scalar to blob role, curve, public share, public key, and party, causing attacker restores scalar into a blob that should not be sign-capable and producing an in-scope cb-mpc bounty impact?

## Target
- File/function: `src/cbmpc/crypto/tdh2.cpp::combine_additive`
- Entrypoint: `coinbase::api::tdh2::combine_ac via include/cbmpc/api/tdh2.h`
- Attacker controls: access_structure, party_names, public_shares, label, partial names, partial decryptions, and ciphertext; specifically public_share_compressed from one blob paired with scalar from another blob while two sessions run concurrently
- Exploit idea: Start from supported public API `coinbase::api::tdh2::combine_ac` in `include/cbmpc/api/tdh2.h` with access_structure, party_names, public_shares, label, partial names, partial decryptions, and ciphertext while two sessions run concurrently. The malicious side supplies public_share_compressed from one blob paired with scalar from another blob. Investigate whether `src/cbmpc/crypto/tdh2.cpp` `combine_additive` assumes attach APIs bind scalar to blob role, curve, public share, public key, and party was already enforced and therefore lets attacker restores scalar into a blob that should not be sign-capable.
- Invariant to test: The TDH2 path must preserve curve, key/blob version, party identity, session or label context, access-structure semantics, and validated encodings from `coinbase::api::tdh2::combine_ac` through `src/cbmpc/crypto/tdh2.cpp`.
- Expected Immunefi impact: Coinbase cb-mpc bounty (HackerOne, not Immunefi): Critical key compromise or significant disclosure/substitution of sensitive key material through supported public APIs.
- Fast validation: Write a local public-API harness with one honest unmodified party and malicious fake transport or buffers; mutate public_share_compressed from one blob paired with scalar from another blob; assert rejection before `src/cbmpc/crypto/tdh2.cpp` `combine_additive` can produce a valid-looking signature, key blob, proof, ciphertext, plaintext, public share, or recovered scalar.
