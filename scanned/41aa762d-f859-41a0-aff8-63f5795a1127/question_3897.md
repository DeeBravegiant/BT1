# Q3897: TDH2 TDH2 partial verification gap in tdh2.h

## Question
Can an unprivileged attacker enter through `coinbase::api::tdh2::combine_ac` with access_structure, party_names, public_shares, label, partial names, partial decryptions, and ciphertext after successful DKG and before signing, reach `include/cbmpc/api/tdh2.h` `dkg_ac`, and use partial decryptions mixed from different ciphertexts, labels, public shares, or party names to bypass the requirement that partial decryptions are checked against exact public key, share, ciphertext, and label, causing TDH2 combine returns plaintext without matching threshold shares and producing an in-scope cb-mpc bounty impact?

## Target
- File/function: `include/cbmpc/api/tdh2.h::dkg_ac`
- Entrypoint: `coinbase::api::tdh2::combine_ac via include/cbmpc/api/tdh2.h`
- Attacker controls: access_structure, party_names, public_shares, label, partial names, partial decryptions, and ciphertext; specifically partial decryptions mixed from different ciphertexts, labels, public shares, or party names after successful DKG and before signing
- Exploit idea: Start from supported public API `coinbase::api::tdh2::combine_ac` in `include/cbmpc/api/tdh2.h` with access_structure, party_names, public_shares, label, partial names, partial decryptions, and ciphertext after successful DKG and before signing. The malicious side supplies partial decryptions mixed from different ciphertexts, labels, public shares, or party names. Investigate whether `include/cbmpc/api/tdh2.h` `dkg_ac` assumes partial decryptions are checked against exact public key, share, ciphertext, and label was already enforced and therefore lets TDH2 combine returns plaintext without matching threshold shares.
- Invariant to test: The TDH2 path must preserve curve, key/blob version, party identity, session or label context, access-structure semantics, and validated encodings from `coinbase::api::tdh2::combine_ac` through `include/cbmpc/api/tdh2.h`.
- Expected Immunefi impact: Coinbase cb-mpc bounty (HackerOne, not Immunefi): Critical key compromise or significant disclosure/substitution of sensitive key material through supported public APIs.
- Fast validation: Write a local public-API harness with one honest unmodified party and malicious fake transport or buffers; mutate partial decryptions mixed from different ciphertexts, labels, public shares, or party names; assert rejection before `include/cbmpc/api/tdh2.h` `dkg_ac` can produce a valid-looking signature, key blob, proof, ciphertext, plaintext, public share, or recovered scalar.
