# Q3256: TDH2 quorum reconstruction mismatch in tdh2.h

## Question
Can an unprivileged attacker enter through `coinbase::api::tdh2::partial_decrypt` with private_share, ciphertext, and label while two sessions run concurrently, reach `include-internal/cbmpc/internal/crypto/tdh2.h` `decrypt`, and use public shares and partial shares with mismatched or reordered party-name vectors to bypass the requirement that share vectors stay aligned with party-name vectors through reconstruction, causing combine/reconstruct accepts shares under the wrong participant mapping and producing an in-scope cb-mpc bounty impact?

## Target
- File/function: `include-internal/cbmpc/internal/crypto/tdh2.h::decrypt`
- Entrypoint: `coinbase::api::tdh2::partial_decrypt via include/cbmpc/api/tdh2.h`
- Attacker controls: private_share, ciphertext, and label; specifically public shares and partial shares with mismatched or reordered party-name vectors while two sessions run concurrently
- Exploit idea: Start from supported public API `coinbase::api::tdh2::partial_decrypt` in `include/cbmpc/api/tdh2.h` with private_share, ciphertext, and label while two sessions run concurrently. The malicious side supplies public shares and partial shares with mismatched or reordered party-name vectors. Investigate whether `include-internal/cbmpc/internal/crypto/tdh2.h` `decrypt` assumes share vectors stay aligned with party-name vectors through reconstruction was already enforced and therefore lets combine/reconstruct accepts shares under the wrong participant mapping.
- Invariant to test: The TDH2 path must preserve curve, key/blob version, party identity, session or label context, access-structure semantics, and validated encodings from `coinbase::api::tdh2::partial_decrypt` through `include-internal/cbmpc/internal/crypto/tdh2.h`.
- Expected Immunefi impact: Coinbase cb-mpc bounty (HackerOne, not Immunefi): Critical key compromise or significant disclosure/substitution of sensitive key material through supported public APIs.
- Fast validation: Write a local public-API harness with one honest unmodified party and malicious fake transport or buffers; mutate public shares and partial shares with mismatched or reordered party-name vectors; assert rejection before `include-internal/cbmpc/internal/crypto/tdh2.h` `decrypt` can produce a valid-looking signature, key blob, proof, ciphertext, plaintext, public share, or recovered scalar.
