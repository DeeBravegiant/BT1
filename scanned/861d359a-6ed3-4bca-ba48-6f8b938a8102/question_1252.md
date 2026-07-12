# Q1252: ECDSA-MP OT transcript swap in ecdsa_mp.h

## Question
Can an unprivileged attacker enter through `coinbase::api::ecdsa_mp::sign_ac` with ac_key_blob, access_structure, msg, sig_receiver, and malicious peer messages during the first accepted protocol run, reach `include-internal/cbmpc/internal/protocol/ecdsa_mp.h` `refresh_ac`, and use pairwise OT messages swapped between roles or signing sessions to bypass the requirement that OT transcripts are bound to role, sid, party index, and signing key, causing ECDSA-MP multiplication leaks share information or yields a forged signature and producing an in-scope cb-mpc bounty impact?

## Target
- File/function: `include-internal/cbmpc/internal/protocol/ecdsa_mp.h::refresh_ac`
- Entrypoint: `coinbase::api::ecdsa_mp::sign_ac via include/cbmpc/api/ecdsa_mp.h`
- Attacker controls: ac_key_blob, access_structure, msg, sig_receiver, and malicious peer messages; specifically pairwise OT messages swapped between roles or signing sessions during the first accepted protocol run
- Exploit idea: Start from supported public API `coinbase::api::ecdsa_mp::sign_ac` in `include/cbmpc/api/ecdsa_mp.h` with ac_key_blob, access_structure, msg, sig_receiver, and malicious peer messages during the first accepted protocol run. The malicious side supplies pairwise OT messages swapped between roles or signing sessions. Investigate whether `include-internal/cbmpc/internal/protocol/ecdsa_mp.h` `refresh_ac` assumes OT transcripts are bound to role, sid, party index, and signing key was already enforced and therefore lets ECDSA-MP multiplication leaks share information or yields a forged signature.
- Invariant to test: The ECDSA-MP path must preserve curve, key/blob version, party identity, session or label context, access-structure semantics, and validated encodings from `coinbase::api::ecdsa_mp::sign_ac` through `include-internal/cbmpc/internal/protocol/ecdsa_mp.h`.
- Expected Immunefi impact: Coinbase cb-mpc bounty (HackerOne, not Immunefi): Critical key compromise or significant disclosure/substitution of sensitive key material through supported public APIs.
- Fast validation: Write a local public-API harness with one honest unmodified party and malicious fake transport or buffers; mutate pairwise OT messages swapped between roles or signing sessions; assert rejection before `include-internal/cbmpc/internal/protocol/ecdsa_mp.h` `refresh_ac` can produce a valid-looking signature, key blob, proof, ciphertext, plaintext, public share, or recovered scalar.
