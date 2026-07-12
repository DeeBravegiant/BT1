# Q2280: ECDSA-MP public-private blob downgrade in ecdsa_mp.h

## Question
Can an unprivileged attacker enter through `coinbase::api::ecdsa_mp::attach_private_scalar` with public_key_blob, fixed scalar, and public share point during backup verification before recovery, reach `include/cbmpc/api/ecdsa_mp.h` `get_public_share_compressed`, and use scalar-detached public blob edited to look like a full signing key blob to bypass the requirement that redacted blobs are tagged and rejected by sign/refresh until attach succeeds, causing signing or refresh uses absent, stale, or attacker-supplied private scalar and producing an in-scope cb-mpc bounty impact?

## Target
- File/function: `include/cbmpc/api/ecdsa_mp.h::get_public_share_compressed`
- Entrypoint: `coinbase::api::ecdsa_mp::attach_private_scalar via include/cbmpc/api/ecdsa_mp.h`
- Attacker controls: public_key_blob, fixed scalar, and public share point; specifically scalar-detached public blob edited to look like a full signing key blob during backup verification before recovery
- Exploit idea: Start from supported public API `coinbase::api::ecdsa_mp::attach_private_scalar` in `include/cbmpc/api/ecdsa_mp.h` with public_key_blob, fixed scalar, and public share point during backup verification before recovery. The malicious side supplies scalar-detached public blob edited to look like a full signing key blob. Investigate whether `include/cbmpc/api/ecdsa_mp.h` `get_public_share_compressed` assumes redacted blobs are tagged and rejected by sign/refresh until attach succeeds was already enforced and therefore lets signing or refresh uses absent, stale, or attacker-supplied private scalar.
- Invariant to test: The ECDSA-MP path must preserve curve, key/blob version, party identity, session or label context, access-structure semantics, and validated encodings from `coinbase::api::ecdsa_mp::attach_private_scalar` through `include/cbmpc/api/ecdsa_mp.h`.
- Expected Immunefi impact: Coinbase cb-mpc bounty (HackerOne, not Immunefi): Critical key compromise or significant disclosure/substitution of sensitive key material through supported public APIs.
- Fast validation: Write a local public-API harness with one honest unmodified party and malicious fake transport or buffers; mutate scalar-detached public blob edited to look like a full signing key blob; assert rejection before `include/cbmpc/api/ecdsa_mp.h` `get_public_share_compressed` can produce a valid-looking signature, key blob, proof, ciphertext, plaintext, public share, or recovered scalar.
