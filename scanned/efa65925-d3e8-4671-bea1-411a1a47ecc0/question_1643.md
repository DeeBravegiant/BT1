# Q1643: ZK proof error-state confusion in elgamal.h

## Question
Can an unprivileged attacker enter through `coinbase::api::ecdsa_mp::sign_ac` with ac_key_blob, access_structure, msg, sig_receiver, and malicious peer messages when labels or sids are reused across supported flows, reach `include-internal/cbmpc/internal/crypto/elgamal.h` `check_curve`, and use input that triggers an inner parse/proof failure after partially filling output buffers to bypass the requirement that outputs are cleared or invalidated on every internal error path, causing a caller receives reusable partial output after validation failure and producing an in-scope cb-mpc bounty impact?

## Target
- File/function: `include-internal/cbmpc/internal/crypto/elgamal.h::check_curve`
- Entrypoint: `coinbase::api::ecdsa_mp::sign_ac via include/cbmpc/api/ecdsa_mp.h`
- Attacker controls: ac_key_blob, access_structure, msg, sig_receiver, and malicious peer messages; specifically input that triggers an inner parse/proof failure after partially filling output buffers when labels or sids are reused across supported flows
- Exploit idea: Start from supported public API `coinbase::api::ecdsa_mp::sign_ac` in `include/cbmpc/api/ecdsa_mp.h` with ac_key_blob, access_structure, msg, sig_receiver, and malicious peer messages when labels or sids are reused across supported flows. The malicious side supplies input that triggers an inner parse/proof failure after partially filling output buffers. Investigate whether `include-internal/cbmpc/internal/crypto/elgamal.h` `check_curve` assumes outputs are cleared or invalidated on every internal error path was already enforced and therefore lets a caller receives reusable partial output after validation failure.
- Invariant to test: The ZK proof path must preserve curve, key/blob version, party identity, session or label context, access-structure semantics, and validated encodings from `coinbase::api::ecdsa_mp::sign_ac` through `include-internal/cbmpc/internal/crypto/elgamal.h`.
- Expected Immunefi impact: Coinbase cb-mpc bounty (HackerOne, not Immunefi): High public-API reachable validation bypass in a supported high-level protocol.
- Fast validation: Write a local public-API harness with one honest unmodified party and malicious fake transport or buffers; mutate input that triggers an inner parse/proof failure after partially filling output buffers; assert rejection before `include-internal/cbmpc/internal/crypto/elgamal.h` `check_curve` can produce a valid-looking signature, key blob, proof, ciphertext, plaintext, public share, or recovered scalar.
