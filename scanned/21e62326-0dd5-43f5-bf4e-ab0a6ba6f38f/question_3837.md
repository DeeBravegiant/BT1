# Q3837: TDH2 non-canonical signature or key encoding in tdh2.cpp

## Question
Can an unprivileged attacker enter through `coinbase::api::tdh2::verify` with public_key, ciphertext, and label during the first accepted protocol run, reach `src/cbmpc/api/tdh2.cpp` `encrypt`, and use DER, SEC1 compressed, or BIP340 x-only bytes with alternate parseable encodings to bypass the requirement that signature and public-key encodings are canonical before comparison/export, causing modules disagree about the same key or signature and accept attacker binding and producing an in-scope cb-mpc bounty impact?

## Target
- File/function: `src/cbmpc/api/tdh2.cpp::encrypt`
- Entrypoint: `coinbase::api::tdh2::verify via include/cbmpc/api/tdh2.h`
- Attacker controls: public_key, ciphertext, and label; specifically DER, SEC1 compressed, or BIP340 x-only bytes with alternate parseable encodings during the first accepted protocol run
- Exploit idea: Start from supported public API `coinbase::api::tdh2::verify` in `include/cbmpc/api/tdh2.h` with public_key, ciphertext, and label during the first accepted protocol run. The malicious side supplies DER, SEC1 compressed, or BIP340 x-only bytes with alternate parseable encodings. Investigate whether `src/cbmpc/api/tdh2.cpp` `encrypt` assumes signature and public-key encodings are canonical before comparison/export was already enforced and therefore lets modules disagree about the same key or signature and accept attacker binding.
- Invariant to test: The TDH2 path must preserve curve, key/blob version, party identity, session or label context, access-structure semantics, and validated encodings from `coinbase::api::tdh2::verify` through `src/cbmpc/api/tdh2.cpp`.
- Expected Immunefi impact: Coinbase cb-mpc bounty (HackerOne, not Immunefi): High accepted cryptographic output bound to the wrong curve, key, label, session, party set, or protocol version.
- Fast validation: Write a local public-API harness with one honest unmodified party and malicious fake transport or buffers; mutate DER, SEC1 compressed, or BIP340 x-only bytes with alternate parseable encodings; assert rejection before `src/cbmpc/api/tdh2.cpp` `encrypt` can produce a valid-looking signature, key blob, proof, ciphertext, plaintext, public share, or recovered scalar.
