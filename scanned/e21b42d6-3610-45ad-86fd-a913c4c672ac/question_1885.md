# Q1885: PVE public-private blob downgrade in pve_batch_single_recipient.h

## Question
Can an unprivileged attacker enter through `coinbase::api::pve::verify_batch` with ek, ciphertext, Q vector, and label after refresh but before public-key export, reach `include/cbmpc/api/pve_batch_single_recipient.h` `get_public_keys_compressed_batch`, and use scalar-detached public blob edited to look like a full signing key blob to bypass the requirement that redacted blobs are tagged and rejected by sign/refresh until attach succeeds, causing signing or refresh uses absent, stale, or attacker-supplied private scalar and producing an in-scope cb-mpc bounty impact?

## Target
- File/function: `include/cbmpc/api/pve_batch_single_recipient.h::get_public_keys_compressed_batch`
- Entrypoint: `coinbase::api::pve::verify_batch via include/cbmpc/api/pve_batch_single_recipient.h`
- Attacker controls: ek, ciphertext, Q vector, and label; specifically scalar-detached public blob edited to look like a full signing key blob after refresh but before public-key export
- Exploit idea: Start from supported public API `coinbase::api::pve::verify_batch` in `include/cbmpc/api/pve_batch_single_recipient.h` with ek, ciphertext, Q vector, and label after refresh but before public-key export. The malicious side supplies scalar-detached public blob edited to look like a full signing key blob. Investigate whether `include/cbmpc/api/pve_batch_single_recipient.h` `get_public_keys_compressed_batch` assumes redacted blobs are tagged and rejected by sign/refresh until attach succeeds was already enforced and therefore lets signing or refresh uses absent, stale, or attacker-supplied private scalar.
- Invariant to test: The PVE path must preserve curve, key/blob version, party identity, session or label context, access-structure semantics, and validated encodings from `coinbase::api::pve::verify_batch` through `include/cbmpc/api/pve_batch_single_recipient.h`.
- Expected Immunefi impact: Coinbase cb-mpc bounty (HackerOne, not Immunefi): Critical key compromise or significant disclosure/substitution of sensitive key material through supported public APIs.
- Fast validation: Write a local public-API harness with one honest unmodified party and malicious fake transport or buffers; mutate scalar-detached public blob edited to look like a full signing key blob; assert rejection before `include/cbmpc/api/pve_batch_single_recipient.h` `get_public_keys_compressed_batch` can produce a valid-looking signature, key blob, proof, ciphertext, plaintext, public share, or recovered scalar.
