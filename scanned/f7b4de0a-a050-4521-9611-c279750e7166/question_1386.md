# Q1386: ECDSA-MP committed broadcast equivocation in ecdsa_mp.h

## Question
Can an unprivileged attacker enter through `coinbase::api::ecdsa_mp::sign_ac` with ac_key_blob, access_structure, msg, sig_receiver, and malicious peer messages while two sessions run concurrently, reach `include-internal/cbmpc/internal/protocol/ecdsa_mp.h` `sign`, and use different packed broadcast payloads sent to different parties in the same round to bypass the requirement that committed broadcast binds payload to sender, recipient set, sid, and round, causing honest parties derive divergent state while one output is accepted and producing an in-scope cb-mpc bounty impact?

## Target
- File/function: `include-internal/cbmpc/internal/protocol/ecdsa_mp.h::sign`
- Entrypoint: `coinbase::api::ecdsa_mp::sign_ac via include/cbmpc/api/ecdsa_mp.h`
- Attacker controls: ac_key_blob, access_structure, msg, sig_receiver, and malicious peer messages; specifically different packed broadcast payloads sent to different parties in the same round while two sessions run concurrently
- Exploit idea: Start from supported public API `coinbase::api::ecdsa_mp::sign_ac` in `include/cbmpc/api/ecdsa_mp.h` with ac_key_blob, access_structure, msg, sig_receiver, and malicious peer messages while two sessions run concurrently. The malicious side supplies different packed broadcast payloads sent to different parties in the same round. Investigate whether `include-internal/cbmpc/internal/protocol/ecdsa_mp.h` `sign` assumes committed broadcast binds payload to sender, recipient set, sid, and round was already enforced and therefore lets honest parties derive divergent state while one output is accepted.
- Invariant to test: The ECDSA-MP path must preserve curve, key/blob version, party identity, session or label context, access-structure semantics, and validated encodings from `coinbase::api::ecdsa_mp::sign_ac` through `include-internal/cbmpc/internal/protocol/ecdsa_mp.h`.
- Expected Immunefi impact: Coinbase cb-mpc bounty (HackerOne, not Immunefi): High public-API reachable validation bypass in a supported high-level protocol.
- Fast validation: Write a local public-API harness with one honest unmodified party and malicious fake transport or buffers; mutate different packed broadcast payloads sent to different parties in the same round; assert rejection before `include-internal/cbmpc/internal/protocol/ecdsa_mp.h` `sign` can produce a valid-looking signature, key blob, proof, ciphertext, plaintext, public share, or recovered scalar.
