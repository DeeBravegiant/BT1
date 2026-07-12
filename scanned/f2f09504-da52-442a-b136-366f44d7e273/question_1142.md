# Q1142: access-structure refresh old-new key mix in access_structure_util.h

## Question
Can an unprivileged attacker enter through `coinbase::api::pve::combine_ac` with ciphertext, attempt_index, label, and quorum_shares when the same caller alternates valid and mutated blobs, reach `src/cbmpc/api/access_structure_util.h` `collect_leaf_names`, and use refresh transcript that mixes old public shares with new private shares to bypass the requirement that refresh preserves the public key while replacing only intended secret shares, causing refreshed blob signs under a key different from exported public key and producing an in-scope cb-mpc bounty impact?

## Target
- File/function: `src/cbmpc/api/access_structure_util.h::collect_leaf_names`
- Entrypoint: `coinbase::api::pve::combine_ac via include/cbmpc/api/pve_batch_ac.h`
- Attacker controls: ciphertext, attempt_index, label, and quorum_shares; specifically refresh transcript that mixes old public shares with new private shares when the same caller alternates valid and mutated blobs
- Exploit idea: Start from supported public API `coinbase::api::pve::combine_ac` in `include/cbmpc/api/pve_batch_ac.h` with ciphertext, attempt_index, label, and quorum_shares when the same caller alternates valid and mutated blobs. The malicious side supplies refresh transcript that mixes old public shares with new private shares. Investigate whether `src/cbmpc/api/access_structure_util.h` `collect_leaf_names` assumes refresh preserves the public key while replacing only intended secret shares was already enforced and therefore lets refreshed blob signs under a key different from exported public key.
- Invariant to test: The access-structure path must preserve curve, key/blob version, party identity, session or label context, access-structure semantics, and validated encodings from `coinbase::api::pve::combine_ac` through `src/cbmpc/api/access_structure_util.h`.
- Expected Immunefi impact: Coinbase cb-mpc bounty (HackerOne, not Immunefi): High accepted cryptographic output bound to the wrong curve, key, label, session, party set, or protocol version.
- Fast validation: Write a local public-API harness with one honest unmodified party and malicious fake transport or buffers; mutate refresh transcript that mixes old public shares with new private shares; assert rejection before `src/cbmpc/api/access_structure_util.h` `collect_leaf_names` can produce a valid-looking signature, key blob, proof, ciphertext, plaintext, public share, or recovered scalar.
