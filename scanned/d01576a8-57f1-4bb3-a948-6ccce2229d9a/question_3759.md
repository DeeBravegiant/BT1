# Q3759: PVE PVE verify/decrypt split in pve_batch_ac.h

## Question
Can an unprivileged attacker enter through `coinbase::api::pve::combine_ac` with ciphertext, attempt_index, label, and quorum_shares after a failed attempt is retried with fresh inputs, reach `include/cbmpc/api/pve_batch_ac.h` `partial_decrypt_ac_attempt_ecies_p256_hsm`, and use PVE ciphertext that fails verification but has valid row and length structure to bypass the requirement that untrusted PVE ciphertexts are verified before scalar reconstruction is trusted, causing wrong private scalar batch is reconstructed or accepted and producing an in-scope cb-mpc bounty impact?

## Target
- File/function: `include/cbmpc/api/pve_batch_ac.h::partial_decrypt_ac_attempt_ecies_p256_hsm`
- Entrypoint: `coinbase::api::pve::combine_ac via include/cbmpc/api/pve_batch_ac.h`
- Attacker controls: ciphertext, attempt_index, label, and quorum_shares; specifically PVE ciphertext that fails verification but has valid row and length structure after a failed attempt is retried with fresh inputs
- Exploit idea: Start from supported public API `coinbase::api::pve::combine_ac` in `include/cbmpc/api/pve_batch_ac.h` with ciphertext, attempt_index, label, and quorum_shares after a failed attempt is retried with fresh inputs. The malicious side supplies PVE ciphertext that fails verification but has valid row and length structure. Investigate whether `include/cbmpc/api/pve_batch_ac.h` `partial_decrypt_ac_attempt_ecies_p256_hsm` assumes untrusted PVE ciphertexts are verified before scalar reconstruction is trusted was already enforced and therefore lets wrong private scalar batch is reconstructed or accepted.
- Invariant to test: The PVE path must preserve curve, key/blob version, party identity, session or label context, access-structure semantics, and validated encodings from `coinbase::api::pve::combine_ac` through `include/cbmpc/api/pve_batch_ac.h`.
- Expected Immunefi impact: Coinbase cb-mpc bounty (HackerOne, not Immunefi): Critical key compromise or significant disclosure/substitution of sensitive key material through supported public APIs.
- Fast validation: Write a local public-API harness with one honest unmodified party and malicious fake transport or buffers; mutate PVE ciphertext that fails verification but has valid row and length structure; assert rejection before `include/cbmpc/api/pve_batch_ac.h` `partial_decrypt_ac_attempt_ecies_p256_hsm` can produce a valid-looking signature, key blob, proof, ciphertext, plaintext, public share, or recovered scalar.
