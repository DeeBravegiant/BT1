# Q1289: PVE refresh old-new key mix in pve_batch_single_recipient.cpp

## Question
Can an unprivileged attacker enter through `coinbase::api::pve::combine_ac` with ciphertext, attempt_index, label, and quorum_shares after successful DKG and before signing, reach `src/cbmpc/api/pve_batch_single_recipient.cpp` `parse_batch_ciphertext`, and use refresh transcript that mixes old public shares with new private shares to bypass the requirement that refresh preserves the public key while replacing only intended secret shares, causing refreshed blob signs under a key different from exported public key and producing an in-scope cb-mpc bounty impact?

## Target
- File/function: `src/cbmpc/api/pve_batch_single_recipient.cpp::parse_batch_ciphertext`
- Entrypoint: `coinbase::api::pve::combine_ac via include/cbmpc/api/pve_batch_ac.h`
- Attacker controls: ciphertext, attempt_index, label, and quorum_shares; specifically refresh transcript that mixes old public shares with new private shares after successful DKG and before signing
- Exploit idea: Start from supported public API `coinbase::api::pve::combine_ac` in `include/cbmpc/api/pve_batch_ac.h` with ciphertext, attempt_index, label, and quorum_shares after successful DKG and before signing. The malicious side supplies refresh transcript that mixes old public shares with new private shares. Investigate whether `src/cbmpc/api/pve_batch_single_recipient.cpp` `parse_batch_ciphertext` assumes refresh preserves the public key while replacing only intended secret shares was already enforced and therefore lets refreshed blob signs under a key different from exported public key.
- Invariant to test: The PVE path must preserve curve, key/blob version, party identity, session or label context, access-structure semantics, and validated encodings from `coinbase::api::pve::combine_ac` through `src/cbmpc/api/pve_batch_single_recipient.cpp`.
- Expected Immunefi impact: Coinbase cb-mpc bounty (HackerOne, not Immunefi): High accepted cryptographic output bound to the wrong curve, key, label, session, party set, or protocol version.
- Fast validation: Write a local public-API harness with one honest unmodified party and malicious fake transport or buffers; mutate refresh transcript that mixes old public shares with new private shares; assert rejection before `src/cbmpc/api/pve_batch_single_recipient.cpp` `parse_batch_ciphertext` can produce a valid-looking signature, key blob, proof, ciphertext, plaintext, public share, or recovered scalar.
