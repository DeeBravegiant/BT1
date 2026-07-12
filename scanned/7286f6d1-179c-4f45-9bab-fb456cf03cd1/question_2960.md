# Q2960: TDH2 label substitution in tdh2.h

## Question
Can an unprivileged attacker enter through `coinbase::api::tdh2::partial_decrypt` with private_share, ciphertext, and label during threshold combine with a minimal quorum, reach `include/cbmpc/api/tdh2.h` `dkg_ac`, and use two attacker-chosen labels with different security contexts to bypass the requirement that labels are authenticated into every ciphertext, proof, partial decryption, and combine operation, causing a ciphertext or share verified for one label is accepted for another and producing an in-scope cb-mpc bounty impact?

## Target
- File/function: `include/cbmpc/api/tdh2.h::dkg_ac`
- Entrypoint: `coinbase::api::tdh2::partial_decrypt via include/cbmpc/api/tdh2.h`
- Attacker controls: private_share, ciphertext, and label; specifically two attacker-chosen labels with different security contexts during threshold combine with a minimal quorum
- Exploit idea: Start from supported public API `coinbase::api::tdh2::partial_decrypt` in `include/cbmpc/api/tdh2.h` with private_share, ciphertext, and label during threshold combine with a minimal quorum. The malicious side supplies two attacker-chosen labels with different security contexts. Investigate whether `include/cbmpc/api/tdh2.h` `dkg_ac` assumes labels are authenticated into every ciphertext, proof, partial decryption, and combine operation was already enforced and therefore lets a ciphertext or share verified for one label is accepted for another.
- Invariant to test: The TDH2 path must preserve curve, key/blob version, party identity, session or label context, access-structure semantics, and validated encodings from `coinbase::api::tdh2::partial_decrypt` through `include/cbmpc/api/tdh2.h`.
- Expected Immunefi impact: Coinbase cb-mpc bounty (HackerOne, not Immunefi): High accepted cryptographic output bound to the wrong curve, key, label, session, party set, or protocol version.
- Fast validation: Write a local public-API harness with one honest unmodified party and malicious fake transport or buffers; mutate two attacker-chosen labels with different security contexts; assert rejection before `include/cbmpc/api/tdh2.h` `dkg_ac` can produce a valid-looking signature, key blob, proof, ciphertext, plaintext, public share, or recovered scalar.
