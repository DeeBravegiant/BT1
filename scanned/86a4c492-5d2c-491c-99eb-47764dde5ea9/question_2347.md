# Q2347: access-structure quorum reconstruction mismatch in access_structure_util.h

## Question
Can an unprivileged attacker enter through `coinbase::api::pve::combine_ac` with ciphertext, attempt_index, label, and quorum_shares when parties disagree on recipient or quorum ordering, reach `src/cbmpc/api/access_structure_util.h` `to_internal_party_set`, and use public shares and partial shares with mismatched or reordered party-name vectors to bypass the requirement that share vectors stay aligned with party-name vectors through reconstruction, causing combine/reconstruct accepts shares under the wrong participant mapping and producing an in-scope cb-mpc bounty impact?

## Target
- File/function: `src/cbmpc/api/access_structure_util.h::to_internal_party_set`
- Entrypoint: `coinbase::api::pve::combine_ac via include/cbmpc/api/pve_batch_ac.h`
- Attacker controls: ciphertext, attempt_index, label, and quorum_shares; specifically public shares and partial shares with mismatched or reordered party-name vectors when parties disagree on recipient or quorum ordering
- Exploit idea: Start from supported public API `coinbase::api::pve::combine_ac` in `include/cbmpc/api/pve_batch_ac.h` with ciphertext, attempt_index, label, and quorum_shares when parties disagree on recipient or quorum ordering. The malicious side supplies public shares and partial shares with mismatched or reordered party-name vectors. Investigate whether `src/cbmpc/api/access_structure_util.h` `to_internal_party_set` assumes share vectors stay aligned with party-name vectors through reconstruction was already enforced and therefore lets combine/reconstruct accepts shares under the wrong participant mapping.
- Invariant to test: The access-structure path must preserve curve, key/blob version, party identity, session or label context, access-structure semantics, and validated encodings from `coinbase::api::pve::combine_ac` through `src/cbmpc/api/access_structure_util.h`.
- Expected Immunefi impact: Coinbase cb-mpc bounty (HackerOne, not Immunefi): Critical key compromise or significant disclosure/substitution of sensitive key material through supported public APIs.
- Fast validation: Write a local public-API harness with one honest unmodified party and malicious fake transport or buffers; mutate public shares and partial shares with mismatched or reordered party-name vectors; assert rejection before `src/cbmpc/api/access_structure_util.h` `to_internal_party_set` can produce a valid-looking signature, key blob, proof, ciphertext, plaintext, public share, or recovered scalar.
