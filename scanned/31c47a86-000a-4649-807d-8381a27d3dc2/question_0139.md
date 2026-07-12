# Q139: EdDSA curve binding drift in eddsa_mp.h

## Question
Can an unprivileged attacker enter through `coinbase::api::eddsa_mp::sign_ac` with ac key blob, access_structure, message, receiver, and peer messages when public extraction is compared with signing output, reach `include/cbmpc/api/eddsa_mp.h` `dkg_ac`, and use a curve_id paired with points or scalars from another supported curve to bypass the requirement that curve identity is checked at parse, proof, reconstruction, and export boundaries, causing accepted output is bound to the wrong curve and producing an in-scope cb-mpc bounty impact?

## Target
- File/function: `include/cbmpc/api/eddsa_mp.h::dkg_ac`
- Entrypoint: `coinbase::api::eddsa_mp::sign_ac via include/cbmpc/api/eddsa_mp.h`
- Attacker controls: ac key blob, access_structure, message, receiver, and peer messages; specifically a curve_id paired with points or scalars from another supported curve when public extraction is compared with signing output
- Exploit idea: Start from supported public API `coinbase::api::eddsa_mp::sign_ac` in `include/cbmpc/api/eddsa_mp.h` with ac key blob, access_structure, message, receiver, and peer messages when public extraction is compared with signing output. The malicious side supplies a curve_id paired with points or scalars from another supported curve. Investigate whether `include/cbmpc/api/eddsa_mp.h` `dkg_ac` assumes curve identity is checked at parse, proof, reconstruction, and export boundaries was already enforced and therefore lets accepted output is bound to the wrong curve.
- Invariant to test: The EdDSA path must preserve curve, key/blob version, party identity, session or label context, access-structure semantics, and validated encodings from `coinbase::api::eddsa_mp::sign_ac` through `include/cbmpc/api/eddsa_mp.h`.
- Expected Immunefi impact: Coinbase cb-mpc bounty (HackerOne, not Immunefi): High accepted cryptographic output bound to the wrong curve, key, label, session, party set, or protocol version.
- Fast validation: Write a local public-API harness with one honest unmodified party and malicious fake transport or buffers; mutate a curve_id paired with points or scalars from another supported curve; assert rejection before `include/cbmpc/api/eddsa_mp.h` `dkg_ac` can produce a valid-looking signature, key blob, proof, ciphertext, plaintext, public share, or recovered scalar.
