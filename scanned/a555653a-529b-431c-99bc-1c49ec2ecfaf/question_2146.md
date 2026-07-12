# Q2146: ECDSA-MP Fischlin challenge domain gap in ecdsa_mp.h

## Question
Can an unprivileged attacker enter through `coinbase::api::ecdsa_mp::sign_ac` with ac_key_blob, access_structure, msg, sig_receiver, and malicious peer messages after successful DKG and before signing, reach `include/cbmpc/api/ecdsa_mp.h` `get_public_key_compressed`, and use two proof statements with colliding serialized challenge inputs to bypass the requirement that Fischlin challenge derivation includes statement fields, sid, aux, curve, and protocol id, causing proof for one statement verifies as another and producing an in-scope cb-mpc bounty impact?

## Target
- File/function: `include/cbmpc/api/ecdsa_mp.h::get_public_key_compressed`
- Entrypoint: `coinbase::api::ecdsa_mp::sign_ac via include/cbmpc/api/ecdsa_mp.h`
- Attacker controls: ac_key_blob, access_structure, msg, sig_receiver, and malicious peer messages; specifically two proof statements with colliding serialized challenge inputs after successful DKG and before signing
- Exploit idea: Start from supported public API `coinbase::api::ecdsa_mp::sign_ac` in `include/cbmpc/api/ecdsa_mp.h` with ac_key_blob, access_structure, msg, sig_receiver, and malicious peer messages after successful DKG and before signing. The malicious side supplies two proof statements with colliding serialized challenge inputs. Investigate whether `include/cbmpc/api/ecdsa_mp.h` `get_public_key_compressed` assumes Fischlin challenge derivation includes statement fields, sid, aux, curve, and protocol id was already enforced and therefore lets proof for one statement verifies as another.
- Invariant to test: The ECDSA-MP path must preserve curve, key/blob version, party identity, session or label context, access-structure semantics, and validated encodings from `coinbase::api::ecdsa_mp::sign_ac` through `include/cbmpc/api/ecdsa_mp.h`.
- Expected Immunefi impact: Coinbase cb-mpc bounty (HackerOne, not Immunefi): High public-API reachable validation bypass in a supported high-level protocol.
- Fast validation: Write a local public-API harness with one honest unmodified party and malicious fake transport or buffers; mutate two proof statements with colliding serialized challenge inputs; assert rejection before `include/cbmpc/api/ecdsa_mp.h` `get_public_key_compressed` can produce a valid-looking signature, key blob, proof, ciphertext, plaintext, public share, or recovered scalar.
