# Q3650: ECC validation non-canonical signature or key encoding in ec25519_core.h

## Question
Can an unprivileged attacker enter through `coinbase::api::pve::verify_batch` with ek, ciphertext, Q vector, and label during backup verification before recovery, reach `include-internal/cbmpc/internal/crypto/ec25519_core.h` `from_bin`, and use DER, SEC1 compressed, or BIP340 x-only bytes with alternate parseable encodings to bypass the requirement that signature and public-key encodings are canonical before comparison/export, causing modules disagree about the same key or signature and accept attacker binding and producing an in-scope cb-mpc bounty impact?

## Target
- File/function: `include-internal/cbmpc/internal/crypto/ec25519_core.h::from_bin`
- Entrypoint: `coinbase::api::pve::verify_batch via include/cbmpc/api/pve_batch_single_recipient.h`
- Attacker controls: ek, ciphertext, Q vector, and label; specifically DER, SEC1 compressed, or BIP340 x-only bytes with alternate parseable encodings during backup verification before recovery
- Exploit idea: Start from supported public API `coinbase::api::pve::verify_batch` in `include/cbmpc/api/pve_batch_single_recipient.h` with ek, ciphertext, Q vector, and label during backup verification before recovery. The malicious side supplies DER, SEC1 compressed, or BIP340 x-only bytes with alternate parseable encodings. Investigate whether `include-internal/cbmpc/internal/crypto/ec25519_core.h` `from_bin` assumes signature and public-key encodings are canonical before comparison/export was already enforced and therefore lets modules disagree about the same key or signature and accept attacker binding.
- Invariant to test: The ECC validation path must preserve curve, key/blob version, party identity, session or label context, access-structure semantics, and validated encodings from `coinbase::api::pve::verify_batch` through `include-internal/cbmpc/internal/crypto/ec25519_core.h`.
- Expected Immunefi impact: Coinbase cb-mpc bounty (HackerOne, not Immunefi): High accepted cryptographic output bound to the wrong curve, key, label, session, party set, or protocol version.
- Fast validation: Write a local public-API harness with one honest unmodified party and malicious fake transport or buffers; mutate DER, SEC1 compressed, or BIP340 x-only bytes with alternate parseable encodings; assert rejection before `include-internal/cbmpc/internal/crypto/ec25519_core.h` `from_bin` can produce a valid-looking signature, key blob, proof, ciphertext, plaintext, public share, or recovered scalar.
