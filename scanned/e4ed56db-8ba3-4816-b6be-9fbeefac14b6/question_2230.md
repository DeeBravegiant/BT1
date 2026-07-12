# Q2230: TDH2 message digest semantic confusion in tdh2.cpp

## Question
Can an unprivileged attacker enter through `coinbase::api::tdh2::verify` with public_key, ciphertext, and label after successful DKG and before signing, reach `src/cbmpc/api/tdh2.cpp` `dkg_ac`, and use signing input with wrong length, leading zeros, or raw-message-versus-digest ambiguity to bypass the requirement that ECDSA/Schnorr enforce exact digest semantics while EdDSA binds raw message, causing valid signature is produced over unintended message bytes and producing an in-scope cb-mpc bounty impact?

## Target
- File/function: `src/cbmpc/api/tdh2.cpp::dkg_ac`
- Entrypoint: `coinbase::api::tdh2::verify via include/cbmpc/api/tdh2.h`
- Attacker controls: public_key, ciphertext, and label; specifically signing input with wrong length, leading zeros, or raw-message-versus-digest ambiguity after successful DKG and before signing
- Exploit idea: Start from supported public API `coinbase::api::tdh2::verify` in `include/cbmpc/api/tdh2.h` with public_key, ciphertext, and label after successful DKG and before signing. The malicious side supplies signing input with wrong length, leading zeros, or raw-message-versus-digest ambiguity. Investigate whether `src/cbmpc/api/tdh2.cpp` `dkg_ac` assumes ECDSA/Schnorr enforce exact digest semantics while EdDSA binds raw message was already enforced and therefore lets valid signature is produced over unintended message bytes.
- Invariant to test: The TDH2 path must preserve curve, key/blob version, party identity, session or label context, access-structure semantics, and validated encodings from `coinbase::api::tdh2::verify` through `src/cbmpc/api/tdh2.cpp`.
- Expected Immunefi impact: Coinbase cb-mpc bounty (HackerOne, not Immunefi): Critical valid signing result without required honest two-party or threshold participation.
- Fast validation: Write a local public-API harness with one honest unmodified party and malicious fake transport or buffers; mutate signing input with wrong length, leading zeros, or raw-message-versus-digest ambiguity; assert rejection before `src/cbmpc/api/tdh2.cpp` `dkg_ac` can produce a valid-looking signature, key blob, proof, ciphertext, plaintext, public share, or recovered scalar.
