# Q101: cb-mpc protocol curve binding drift in base_mod.cpp

## Question
Can an unprivileged attacker enter through `coinbase::api::ecdsa_2p::sign` with key_blob, msg_hash, sid, and malicious two-party transcript during backup verification before recovery, reach `src/cbmpc/crypto/base_mod.cpp` `base_mod module`, and use a curve_id paired with points or scalars from another supported curve to bypass the requirement that curve identity is checked at parse, proof, reconstruction, and export boundaries, causing accepted output is bound to the wrong curve and producing an in-scope cb-mpc bounty impact?

## Target
- File/function: `src/cbmpc/crypto/base_mod.cpp::base_mod module`
- Entrypoint: `coinbase::api::ecdsa_2p::sign via include/cbmpc/api/ecdsa_2p.h`
- Attacker controls: key_blob, msg_hash, sid, and malicious two-party transcript; specifically a curve_id paired with points or scalars from another supported curve during backup verification before recovery
- Exploit idea: Start from supported public API `coinbase::api::ecdsa_2p::sign` in `include/cbmpc/api/ecdsa_2p.h` with key_blob, msg_hash, sid, and malicious two-party transcript during backup verification before recovery. The malicious side supplies a curve_id paired with points or scalars from another supported curve. Investigate whether `src/cbmpc/crypto/base_mod.cpp` `base_mod module` assumes curve identity is checked at parse, proof, reconstruction, and export boundaries was already enforced and therefore lets accepted output is bound to the wrong curve.
- Invariant to test: The cb-mpc protocol path must preserve curve, key/blob version, party identity, session or label context, access-structure semantics, and validated encodings from `coinbase::api::ecdsa_2p::sign` through `src/cbmpc/crypto/base_mod.cpp`.
- Expected Immunefi impact: Coinbase cb-mpc bounty (HackerOne, not Immunefi): High accepted cryptographic output bound to the wrong curve, key, label, session, party set, or protocol version.
- Fast validation: Write a local public-API harness with one honest unmodified party and malicious fake transport or buffers; mutate a curve_id paired with points or scalars from another supported curve; assert rejection before `src/cbmpc/crypto/base_mod.cpp` `base_mod module` can produce a valid-looking signature, key blob, proof, ciphertext, plaintext, public share, or recovered scalar.
