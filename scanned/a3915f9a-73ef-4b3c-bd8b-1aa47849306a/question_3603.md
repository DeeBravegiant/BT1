# Q3603: cb-mpc protocol proof flag trust gap in ot.cpp

## Question
Can an unprivileged attacker enter through `coinbase::api::ecdsa_mp::sign_ac` with ac_key_blob, access_structure, msg, sig_receiver, and malicious peer messages during backup verification before recovery, reach `src/cbmpc/protocol/ot.cpp` `sender_random_output_S`, and use proof messages that imply prerequisite proof flags without the prerequisite transcript to bypass the requirement that every prerequisite ZK statement is established before dependent flags are trusted, causing an invalid cryptographic statement feeds an accepted protocol output and producing an in-scope cb-mpc bounty impact?

## Target
- File/function: `src/cbmpc/protocol/ot.cpp::sender_random_output_S`
- Entrypoint: `coinbase::api::ecdsa_mp::sign_ac via include/cbmpc/api/ecdsa_mp.h`
- Attacker controls: ac_key_blob, access_structure, msg, sig_receiver, and malicious peer messages; specifically proof messages that imply prerequisite proof flags without the prerequisite transcript during backup verification before recovery
- Exploit idea: Start from supported public API `coinbase::api::ecdsa_mp::sign_ac` in `include/cbmpc/api/ecdsa_mp.h` with ac_key_blob, access_structure, msg, sig_receiver, and malicious peer messages during backup verification before recovery. The malicious side supplies proof messages that imply prerequisite proof flags without the prerequisite transcript. Investigate whether `src/cbmpc/protocol/ot.cpp` `sender_random_output_S` assumes every prerequisite ZK statement is established before dependent flags are trusted was already enforced and therefore lets an invalid cryptographic statement feeds an accepted protocol output.
- Invariant to test: The cb-mpc protocol path must preserve curve, key/blob version, party identity, session or label context, access-structure semantics, and validated encodings from `coinbase::api::ecdsa_mp::sign_ac` through `src/cbmpc/protocol/ot.cpp`.
- Expected Immunefi impact: Coinbase cb-mpc bounty (HackerOne, not Immunefi): High public-API reachable validation bypass in a supported high-level protocol.
- Fast validation: Write a local public-API harness with one honest unmodified party and malicious fake transport or buffers; mutate proof messages that imply prerequisite proof flags without the prerequisite transcript; assert rejection before `src/cbmpc/protocol/ot.cpp` `sender_random_output_S` can produce a valid-looking signature, key blob, proof, ciphertext, plaintext, public share, or recovered scalar.
