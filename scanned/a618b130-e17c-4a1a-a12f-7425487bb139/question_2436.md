# Q2436: ECC validation public share substitution in base_ecc.h

## Question
Can an unprivileged attacker enter through `coinbase::api::pve::decrypt_batch` with dk, ek, ciphertext, and label during threshold combine with a minimal quorum, reach `include-internal/cbmpc/internal/crypto/base_ecc.h` `verify_all`, and use public_share_compressed from one blob paired with scalar from another blob to bypass the requirement that attach APIs bind scalar to blob role, curve, public share, public key, and party, causing attacker restores scalar into a blob that should not be sign-capable and producing an in-scope cb-mpc bounty impact?

## Target
- File/function: `include-internal/cbmpc/internal/crypto/base_ecc.h::verify_all`
- Entrypoint: `coinbase::api::pve::decrypt_batch via include/cbmpc/api/pve_batch_single_recipient.h`
- Attacker controls: dk, ek, ciphertext, and label; specifically public_share_compressed from one blob paired with scalar from another blob during threshold combine with a minimal quorum
- Exploit idea: Start from supported public API `coinbase::api::pve::decrypt_batch` in `include/cbmpc/api/pve_batch_single_recipient.h` with dk, ek, ciphertext, and label during threshold combine with a minimal quorum. The malicious side supplies public_share_compressed from one blob paired with scalar from another blob. Investigate whether `include-internal/cbmpc/internal/crypto/base_ecc.h` `verify_all` assumes attach APIs bind scalar to blob role, curve, public share, public key, and party was already enforced and therefore lets attacker restores scalar into a blob that should not be sign-capable.
- Invariant to test: The ECC validation path must preserve curve, key/blob version, party identity, session or label context, access-structure semantics, and validated encodings from `coinbase::api::pve::decrypt_batch` through `include-internal/cbmpc/internal/crypto/base_ecc.h`.
- Expected Immunefi impact: Coinbase cb-mpc bounty (HackerOne, not Immunefi): Critical key compromise or significant disclosure/substitution of sensitive key material through supported public APIs.
- Fast validation: Write a local public-API harness with one honest unmodified party and malicious fake transport or buffers; mutate public_share_compressed from one blob paired with scalar from another blob; assert rejection before `include-internal/cbmpc/internal/crypto/base_ecc.h` `verify_all` can produce a valid-looking signature, key blob, proof, ciphertext, plaintext, public share, or recovered scalar.
