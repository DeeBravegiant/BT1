# Q3034: TDH2 label substitution in tdh2.cpp

## Question
Can an unprivileged attacker enter through `coinbase::api::tdh2::partial_decrypt` with private_share, ciphertext, and label when public extraction is compared with signing output, reach `src/cbmpc/api/tdh2.cpp` `validate_public_key`, and use two attacker-chosen labels with different security contexts to bypass the requirement that labels are authenticated into every ciphertext, proof, partial decryption, and combine operation, causing a ciphertext or share verified for one label is accepted for another and producing an in-scope cb-mpc bounty impact?

## Target
- File/function: `src/cbmpc/api/tdh2.cpp::validate_public_key`
- Entrypoint: `coinbase::api::tdh2::partial_decrypt via include/cbmpc/api/tdh2.h`
- Attacker controls: private_share, ciphertext, and label; specifically two attacker-chosen labels with different security contexts when public extraction is compared with signing output
- Exploit idea: Start from supported public API `coinbase::api::tdh2::partial_decrypt` in `include/cbmpc/api/tdh2.h` with private_share, ciphertext, and label when public extraction is compared with signing output. The malicious side supplies two attacker-chosen labels with different security contexts. Investigate whether `src/cbmpc/api/tdh2.cpp` `validate_public_key` assumes labels are authenticated into every ciphertext, proof, partial decryption, and combine operation was already enforced and therefore lets a ciphertext or share verified for one label is accepted for another.
- Invariant to test: The TDH2 path must preserve curve, key/blob version, party identity, session or label context, access-structure semantics, and validated encodings from `coinbase::api::tdh2::partial_decrypt` through `src/cbmpc/api/tdh2.cpp`.
- Expected Immunefi impact: Coinbase cb-mpc bounty (HackerOne, not Immunefi): High accepted cryptographic output bound to the wrong curve, key, label, session, party set, or protocol version.
- Fast validation: Write a local public-API harness with one honest unmodified party and malicious fake transport or buffers; mutate two attacker-chosen labels with different security contexts; assert rejection before `src/cbmpc/api/tdh2.cpp` `validate_public_key` can produce a valid-looking signature, key blob, proof, ciphertext, plaintext, public share, or recovered scalar.
