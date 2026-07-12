# Q147: TDH2 curve binding drift in tdh2.h

## Question
Can an unprivileged attacker enter through `coinbase::api::tdh2::partial_decrypt` with private_share, ciphertext, and label while two sessions run concurrently, reach `include/cbmpc/api/tdh2.h` `dkg_ac`, and use a curve_id paired with points or scalars from another supported curve to bypass the requirement that curve identity is checked at parse, proof, reconstruction, and export boundaries, causing accepted output is bound to the wrong curve and producing an in-scope cb-mpc bounty impact?

## Target
- File/function: `include/cbmpc/api/tdh2.h::dkg_ac`
- Entrypoint: `coinbase::api::tdh2::partial_decrypt via include/cbmpc/api/tdh2.h`
- Attacker controls: private_share, ciphertext, and label; specifically a curve_id paired with points or scalars from another supported curve while two sessions run concurrently
- Exploit idea: Start from supported public API `coinbase::api::tdh2::partial_decrypt` in `include/cbmpc/api/tdh2.h` with private_share, ciphertext, and label while two sessions run concurrently. The malicious side supplies a curve_id paired with points or scalars from another supported curve. Investigate whether `include/cbmpc/api/tdh2.h` `dkg_ac` assumes curve identity is checked at parse, proof, reconstruction, and export boundaries was already enforced and therefore lets accepted output is bound to the wrong curve.
- Invariant to test: The TDH2 path must preserve curve, key/blob version, party identity, session or label context, access-structure semantics, and validated encodings from `coinbase::api::tdh2::partial_decrypt` through `include/cbmpc/api/tdh2.h`.
- Expected Immunefi impact: Coinbase cb-mpc bounty (HackerOne, not Immunefi): High accepted cryptographic output bound to the wrong curve, key, label, session, party set, or protocol version.
- Fast validation: Write a local public-API harness with one honest unmodified party and malicious fake transport or buffers; mutate a curve_id paired with points or scalars from another supported curve; assert rejection before `include/cbmpc/api/tdh2.h` `dkg_ac` can produce a valid-looking signature, key blob, proof, ciphertext, plaintext, public share, or recovered scalar.
