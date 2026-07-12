# Q2923: TDH2 curve binding drift in tdh2.cpp

## Question
Can an unprivileged attacker enter through `coinbase::api::tdh2::combine_ac` with access_structure, party_names, public_shares, label, partial names, partial decryptions, and ciphertext while two sessions run concurrently, reach `src/cbmpc/crypto/tdh2.cpp` `decrypt`, and use a curve_id paired with points or scalars from another supported curve to bypass the requirement that curve identity is checked at parse, proof, reconstruction, and export boundaries, causing accepted output is bound to the wrong curve and producing an in-scope cb-mpc bounty impact?

## Target
- File/function: `src/cbmpc/crypto/tdh2.cpp::decrypt`
- Entrypoint: `coinbase::api::tdh2::combine_ac via include/cbmpc/api/tdh2.h`
- Attacker controls: access_structure, party_names, public_shares, label, partial names, partial decryptions, and ciphertext; specifically a curve_id paired with points or scalars from another supported curve while two sessions run concurrently
- Exploit idea: Start from supported public API `coinbase::api::tdh2::combine_ac` in `include/cbmpc/api/tdh2.h` with access_structure, party_names, public_shares, label, partial names, partial decryptions, and ciphertext while two sessions run concurrently. The malicious side supplies a curve_id paired with points or scalars from another supported curve. Investigate whether `src/cbmpc/crypto/tdh2.cpp` `decrypt` assumes curve identity is checked at parse, proof, reconstruction, and export boundaries was already enforced and therefore lets accepted output is bound to the wrong curve.
- Invariant to test: The TDH2 path must preserve curve, key/blob version, party identity, session or label context, access-structure semantics, and validated encodings from `coinbase::api::tdh2::combine_ac` through `src/cbmpc/crypto/tdh2.cpp`.
- Expected Immunefi impact: Coinbase cb-mpc bounty (HackerOne, not Immunefi): High accepted cryptographic output bound to the wrong curve, key, label, session, party set, or protocol version.
- Fast validation: Write a local public-API harness with one honest unmodified party and malicious fake transport or buffers; mutate a curve_id paired with points or scalars from another supported curve; assert rejection before `src/cbmpc/crypto/tdh2.cpp` `decrypt` can produce a valid-looking signature, key blob, proof, ciphertext, plaintext, public share, or recovered scalar.
