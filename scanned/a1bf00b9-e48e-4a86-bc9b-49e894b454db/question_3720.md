# Q3720: PVE PVE verify/decrypt split in base_rsa_oaep.cpp

## Question
Can an unprivileged attacker enter through `coinbase::api::pve::decrypt_batch` with dk, ek, ciphertext, and label after refresh but before public-key export, reach `src/cbmpc/crypto/base_rsa_oaep.cpp` `decrypt_oaep`, and use PVE ciphertext that fails verification but has valid row and length structure to bypass the requirement that untrusted PVE ciphertexts are verified before scalar reconstruction is trusted, causing wrong private scalar batch is reconstructed or accepted and producing an in-scope cb-mpc bounty impact?

## Target
- File/function: `src/cbmpc/crypto/base_rsa_oaep.cpp::decrypt_oaep`
- Entrypoint: `coinbase::api::pve::decrypt_batch via include/cbmpc/api/pve_batch_single_recipient.h`
- Attacker controls: dk, ek, ciphertext, and label; specifically PVE ciphertext that fails verification but has valid row and length structure after refresh but before public-key export
- Exploit idea: Start from supported public API `coinbase::api::pve::decrypt_batch` in `include/cbmpc/api/pve_batch_single_recipient.h` with dk, ek, ciphertext, and label after refresh but before public-key export. The malicious side supplies PVE ciphertext that fails verification but has valid row and length structure. Investigate whether `src/cbmpc/crypto/base_rsa_oaep.cpp` `decrypt_oaep` assumes untrusted PVE ciphertexts are verified before scalar reconstruction is trusted was already enforced and therefore lets wrong private scalar batch is reconstructed or accepted.
- Invariant to test: The PVE path must preserve curve, key/blob version, party identity, session or label context, access-structure semantics, and validated encodings from `coinbase::api::pve::decrypt_batch` through `src/cbmpc/crypto/base_rsa_oaep.cpp`.
- Expected Immunefi impact: Coinbase cb-mpc bounty (HackerOne, not Immunefi): Critical key compromise or significant disclosure/substitution of sensitive key material through supported public APIs.
- Fast validation: Write a local public-API harness with one honest unmodified party and malicious fake transport or buffers; mutate PVE ciphertext that fails verification but has valid row and length structure; assert rejection before `src/cbmpc/crypto/base_rsa_oaep.cpp` `decrypt_oaep` can produce a valid-looking signature, key blob, proof, ciphertext, plaintext, public share, or recovered scalar.
