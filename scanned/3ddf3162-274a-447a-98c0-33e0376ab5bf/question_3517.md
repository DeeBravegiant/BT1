# Q3517: ZK proof curve binding drift in elgamal.h

## Question
Can an unprivileged attacker enter through `coinbase::api::pve::verify_batch` with ek, ciphertext, Q vector, and label after refresh but before public-key export, reach `include-internal/cbmpc/internal/crypto/elgamal.h` `check_curve`, and use a curve_id paired with points or scalars from another supported curve to bypass the requirement that curve identity is checked at parse, proof, reconstruction, and export boundaries, causing accepted output is bound to the wrong curve and producing an in-scope cb-mpc bounty impact?

## Target
- File/function: `include-internal/cbmpc/internal/crypto/elgamal.h::check_curve`
- Entrypoint: `coinbase::api::pve::verify_batch via include/cbmpc/api/pve_batch_single_recipient.h`
- Attacker controls: ek, ciphertext, Q vector, and label; specifically a curve_id paired with points or scalars from another supported curve after refresh but before public-key export
- Exploit idea: Start from supported public API `coinbase::api::pve::verify_batch` in `include/cbmpc/api/pve_batch_single_recipient.h` with ek, ciphertext, Q vector, and label after refresh but before public-key export. The malicious side supplies a curve_id paired with points or scalars from another supported curve. Investigate whether `include-internal/cbmpc/internal/crypto/elgamal.h` `check_curve` assumes curve identity is checked at parse, proof, reconstruction, and export boundaries was already enforced and therefore lets accepted output is bound to the wrong curve.
- Invariant to test: The ZK proof path must preserve curve, key/blob version, party identity, session or label context, access-structure semantics, and validated encodings from `coinbase::api::pve::verify_batch` through `include-internal/cbmpc/internal/crypto/elgamal.h`.
- Expected Immunefi impact: Coinbase cb-mpc bounty (HackerOne, not Immunefi): High accepted cryptographic output bound to the wrong curve, key, label, session, party set, or protocol version.
- Fast validation: Write a local public-API harness with one honest unmodified party and malicious fake transport or buffers; mutate a curve_id paired with points or scalars from another supported curve; assert rejection before `include-internal/cbmpc/internal/crypto/elgamal.h` `check_curve` can produce a valid-looking signature, key blob, proof, ciphertext, plaintext, public share, or recovered scalar.
