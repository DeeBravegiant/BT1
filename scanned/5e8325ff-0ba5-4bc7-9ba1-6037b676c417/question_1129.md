# Q1129: PVE refresh old-new key mix in pve_batch.h

## Question
Can an unprivileged attacker enter through `coinbase::api::pve::verify_ac` with ciphertext, expected Qs, access_structure, leaf keys, and label while one malicious peer deviates and one honest party is unmodified, reach `include-internal/cbmpc/internal/protocol/pve_batch.h` `encrypt`, and use refresh transcript that mixes old public shares with new private shares to bypass the requirement that refresh preserves the public key while replacing only intended secret shares, causing refreshed blob signs under a key different from exported public key and producing an in-scope cb-mpc bounty impact?

## Target
- File/function: `include-internal/cbmpc/internal/protocol/pve_batch.h::encrypt`
- Entrypoint: `coinbase::api::pve::verify_ac via include/cbmpc/api/pve_batch_ac.h`
- Attacker controls: ciphertext, expected Qs, access_structure, leaf keys, and label; specifically refresh transcript that mixes old public shares with new private shares while one malicious peer deviates and one honest party is unmodified
- Exploit idea: Start from supported public API `coinbase::api::pve::verify_ac` in `include/cbmpc/api/pve_batch_ac.h` with ciphertext, expected Qs, access_structure, leaf keys, and label while one malicious peer deviates and one honest party is unmodified. The malicious side supplies refresh transcript that mixes old public shares with new private shares. Investigate whether `include-internal/cbmpc/internal/protocol/pve_batch.h` `encrypt` assumes refresh preserves the public key while replacing only intended secret shares was already enforced and therefore lets refreshed blob signs under a key different from exported public key.
- Invariant to test: The PVE path must preserve curve, key/blob version, party identity, session or label context, access-structure semantics, and validated encodings from `coinbase::api::pve::verify_ac` through `include-internal/cbmpc/internal/protocol/pve_batch.h`.
- Expected Immunefi impact: Coinbase cb-mpc bounty (HackerOne, not Immunefi): High accepted cryptographic output bound to the wrong curve, key, label, session, party set, or protocol version.
- Fast validation: Write a local public-API harness with one honest unmodified party and malicious fake transport or buffers; mutate refresh transcript that mixes old public shares with new private shares; assert rejection before `include-internal/cbmpc/internal/protocol/pve_batch.h` `encrypt` can produce a valid-looking signature, key blob, proof, ciphertext, plaintext, public share, or recovered scalar.
