import json
import os

from decouple import config

# todo: if scope_files is: 500 > 50, 300 > 30 , 100 > 10
MAX_REPO = 20
# todo: the path from https:///github.com/dfinity/ICRC-1
SOURCE_REPO = "coinbase/cb-mpc"
# todo: the name of the repository
REPO_NAME = "cb-mpc"
run_number = os.environ.get('GITHUB_RUN_NUMBER') or os.environ.get('CI_PIPELINE_IID', '0')


def get_cyclic_index(run_number, max_index=100):
    """Convert run number to a cyclic index between 1 and max_index"""
    return (int(run_number) - 1) % max_index + 1


def load_repository_urls():
    """Load repository URLs from repositories.json."""
    repo_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "repositories.json")
    if not os.path.exists(repo_file):
        return []

    try:
        with open(repo_file, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return []

    if not isinstance(data, list):
        return []

    return [url for url in data if isinstance(url, str) and url.strip()]


if run_number == "0":
    BASE_URL = f"https://deepwiki.com/{SOURCE_REPO}"
else:
    repository_urls = load_repository_urls()
    if repository_urls:
        run_index = get_cyclic_index(run_number, len(repository_urls))
        BASE_URL = repository_urls[run_index - 1]
    else:
        BASE_URL = f"https://deepwiki.com/{SOURCE_REPO}"

scope_files = [
    "include-internal/cbmpc/internal/core/convert.h",
    "include-internal/cbmpc/internal/core/extended_uint.h",
    "include-internal/cbmpc/internal/core/log.h",
    "include-internal/cbmpc/internal/core/strext.h",
    "include-internal/cbmpc/internal/core/utils.h",
    "include-internal/cbmpc/internal/crypto/base.h",
    "include-internal/cbmpc/internal/crypto/base_bn.h",
    "include-internal/cbmpc/internal/crypto/base_bn256.h",
    "include-internal/cbmpc/internal/crypto/base_ec_core.h",
    "include-internal/cbmpc/internal/crypto/base_ecc.h",
    "include-internal/cbmpc/internal/crypto/base_ecc_secp256k1.h",
    "include-internal/cbmpc/internal/crypto/base_eddsa.h",
    "include-internal/cbmpc/internal/crypto/base_hash.h",
    "include-internal/cbmpc/internal/crypto/base_mod.h",
    "include-internal/cbmpc/internal/crypto/base_paillier.h",
    "include-internal/cbmpc/internal/crypto/base_pki.h",
    "include-internal/cbmpc/internal/crypto/base_rsa.h",
    "include-internal/cbmpc/internal/crypto/commitment.h",
    "include-internal/cbmpc/internal/crypto/ec25519_core.h",
    "include-internal/cbmpc/internal/crypto/elgamal.h",
    "include-internal/cbmpc/internal/crypto/lagrange.h",
    "include-internal/cbmpc/internal/crypto/mlkem768.h",
    "include-internal/cbmpc/internal/crypto/ro.h",
    "include-internal/cbmpc/internal/crypto/scope.h",
    "include-internal/cbmpc/internal/crypto/secret_sharing.h",
    "include-internal/cbmpc/internal/crypto/tdh2.h",
    "include-internal/cbmpc/internal/protocol/agree_random.h",
    "include-internal/cbmpc/internal/protocol/committed_broadcast.h",
    "include-internal/cbmpc/internal/protocol/ec_dkg.h",
    "include-internal/cbmpc/internal/protocol/ecdsa_2p.h",
    "include-internal/cbmpc/internal/protocol/ecdsa_mp.h",
    "include-internal/cbmpc/internal/protocol/eddsa.h",
    "include-internal/cbmpc/internal/protocol/hd_keyset_ecdsa_2p.h",
    "include-internal/cbmpc/internal/protocol/hd_keyset_eddsa_2p.h",
    "include-internal/cbmpc/internal/protocol/hd_tree_bip32.h",
    "include-internal/cbmpc/internal/protocol/int_commitment.h",
    "include-internal/cbmpc/internal/protocol/mpc_job.h",
    "include-internal/cbmpc/internal/protocol/ot.h",
    "include-internal/cbmpc/internal/protocol/pve.h",
    "include-internal/cbmpc/internal/protocol/pve_ac.h",
    "include-internal/cbmpc/internal/protocol/pve_base.h",
    "include-internal/cbmpc/internal/protocol/pve_batch.h",
    "include-internal/cbmpc/internal/protocol/schnorr_2p.h",
    "include-internal/cbmpc/internal/protocol/schnorr_mp.h",
    "include-internal/cbmpc/internal/protocol/sid.h",
    "include-internal/cbmpc/internal/protocol/util.h",
    "include-internal/cbmpc/internal/zk/fischlin.h",
    "include-internal/cbmpc/internal/zk/small_primes.h",
    "include-internal/cbmpc/internal/zk/zk_ec.h",
    "include-internal/cbmpc/internal/zk/zk_elgamal_com.h",
    "include-internal/cbmpc/internal/zk/zk_paillier.h",
    "include-internal/cbmpc/internal/zk/zk_pedersen.h",
    "include-internal/cbmpc/internal/zk/zk_unknown_order.h",
    "include-internal/cbmpc/internal/zk/zk_util.h",
    "include/cbmpc/api/curve.h",
    "include/cbmpc/api/ecdsa_2p.h",
    "include/cbmpc/api/ecdsa_mp.h",
    "include/cbmpc/api/eddsa_2p.h",
    "include/cbmpc/api/eddsa_mp.h",
    "include/cbmpc/api/hd_keyset_ecdsa_2p.h",
    "include/cbmpc/api/hd_keyset_eddsa_2p.h",
    "include/cbmpc/api/pve_base_pke.h",
    "include/cbmpc/api/pve_batch_ac.h",
    "include/cbmpc/api/pve_batch_single_recipient.h",
    "include/cbmpc/api/schnorr_2p.h",
    "include/cbmpc/api/schnorr_mp.h",
    "include/cbmpc/api/tdh2.h",
    "include/cbmpc/c_api/access_structure.h",
    "include/cbmpc/c_api/cmem.h",
    "include/cbmpc/c_api/common.h",
    "include/cbmpc/c_api/ecdsa_2p.h",
    "include/cbmpc/c_api/ecdsa_mp.h",
    "include/cbmpc/c_api/eddsa_2p.h",
    "include/cbmpc/c_api/eddsa_mp.h",
    "include/cbmpc/c_api/job.h",
    "include/cbmpc/c_api/pve_base_pke.h",
    "include/cbmpc/c_api/pve_batch_ac.h",
    "include/cbmpc/c_api/pve_batch_single_recipient.h",
    "include/cbmpc/c_api/schnorr_2p.h",
    "include/cbmpc/c_api/schnorr_mp.h",
    "include/cbmpc/c_api/tdh2.h",
    "include/cbmpc/core/access_structure.h",
    "include/cbmpc/core/bip32_path.h",
    "include/cbmpc/core/buf.h",
    "include/cbmpc/core/buf128.h",
    "include/cbmpc/core/buf256.h",
    "include/cbmpc/core/error.h",
    "include/cbmpc/core/job.h",
    "include/cbmpc/core/macros.h",
    "include/cbmpc/core/precompiled.h",
    "src/cbmpc/api/access_structure_util.h",
    "src/cbmpc/api/curve_util.h",
    "src/cbmpc/api/ecdsa2pc.cpp",
    "src/cbmpc/api/ecdsa_mp.cpp",
    "src/cbmpc/api/eddsa2pc.cpp",
    "src/cbmpc/api/eddsa_mp.cpp",
    "src/cbmpc/api/hd_keyset_ecdsa_2p.cpp",
    "src/cbmpc/api/hd_keyset_eddsa_2p.cpp",
    "src/cbmpc/api/hd_keyset_util.h",
    "src/cbmpc/api/job_util.h",
    "src/cbmpc/api/mem_util.h",
    "src/cbmpc/api/pve_base_pke.cpp",
    "src/cbmpc/api/pve_batch_ac.cpp",
    "src/cbmpc/api/pve_batch_single_recipient.cpp",
    "src/cbmpc/api/pve_internal.h",
    "src/cbmpc/api/schnorr2pc.cpp",
    "src/cbmpc/api/schnorr_mp.cpp",
    "src/cbmpc/api/tdh2.cpp",
    "src/cbmpc/c_api/access_structure_adapter.h",
    "src/cbmpc/c_api/common.cpp",
    "src/cbmpc/c_api/ecdsa2pc.cpp",
    "src/cbmpc/c_api/ecdsa_mp.cpp",
    "src/cbmpc/c_api/eddsa2pc.cpp",
    "src/cbmpc/c_api/eddsa_mp.cpp",
    "src/cbmpc/c_api/pve_base_pke.cpp",
    "src/cbmpc/c_api/pve_batch_ac.cpp",
    "src/cbmpc/c_api/pve_batch_single_recipient.cpp",
    "src/cbmpc/c_api/pve_internal.h",
    "src/cbmpc/c_api/schnorr2pc.cpp",
    "src/cbmpc/c_api/schnorr_mp.cpp",
    "src/cbmpc/c_api/tdh2.cpp",
    "src/cbmpc/c_api/transport_adapter.h",
    "src/cbmpc/c_api/util.h",
    "src/cbmpc/core/buf.cpp",
    "src/cbmpc/core/buf128.cpp",
    "src/cbmpc/core/buf256.cpp",
    "src/cbmpc/core/convert.cpp",
    "src/cbmpc/core/error.cpp",
    "src/cbmpc/core/strext.cpp",
    "src/cbmpc/crypto/base.cpp",
    "src/cbmpc/crypto/base_bn.cpp",
    "src/cbmpc/crypto/base_bn256.cpp",
    "src/cbmpc/crypto/base_ec_core.cpp",
    "src/cbmpc/crypto/base_ecc.cpp",
    "src/cbmpc/crypto/base_ecc_secp256k1.cpp",
    "src/cbmpc/crypto/base_eddsa.cpp",
    "src/cbmpc/crypto/base_hash.cpp",
    "src/cbmpc/crypto/base_mod.cpp",
    "src/cbmpc/crypto/base_paillier.cpp",
    "src/cbmpc/crypto/base_rsa.cpp",
    "src/cbmpc/crypto/base_rsa_oaep.cpp",
    "src/cbmpc/crypto/drbg.cpp",
    "src/cbmpc/crypto/ec25519_core.cpp",
    "src/cbmpc/crypto/elgamal.cpp",
    "src/cbmpc/crypto/lagrange.cpp",
    "src/cbmpc/crypto/mlkem768.cpp",
    "src/cbmpc/crypto/ro.cpp",
    "src/cbmpc/crypto/secret_sharing.cpp",
    "src/cbmpc/crypto/tdh2.cpp",
    "src/cbmpc/protocol/agree_random.cpp",
    "src/cbmpc/protocol/ec_dkg.cpp",
    "src/cbmpc/protocol/ecdsa_2p.cpp",
    "src/cbmpc/protocol/ecdsa_mp.cpp",
    "src/cbmpc/protocol/eddsa.cpp",
    "src/cbmpc/protocol/hd_keyset_ecdsa_2p.cpp",
    "src/cbmpc/protocol/hd_keyset_eddsa_2p.cpp",
    "src/cbmpc/protocol/hd_tree_bip32.cpp",
    "src/cbmpc/protocol/int_commitment.cpp",
    "src/cbmpc/protocol/mpc_job.cpp",
    "src/cbmpc/protocol/ot.cpp",
    "src/cbmpc/protocol/pve.cpp",
    "src/cbmpc/protocol/pve_ac.cpp",
    "src/cbmpc/protocol/pve_base.cpp",
    "src/cbmpc/protocol/pve_batch.cpp",
    "src/cbmpc/protocol/schnorr_2p.cpp",
    "src/cbmpc/protocol/schnorr_mp.cpp",
    "src/cbmpc/zk/fischlin.cpp",
    "src/cbmpc/zk/small_primes.cpp",
    "src/cbmpc/zk/zk_ec.cpp",
    "src/cbmpc/zk/zk_elgamal_com.cpp",
    "src/cbmpc/zk/zk_paillier.cpp",
    "src/cbmpc/zk/zk_pedersen.cpp",
    "src/cbmpc/zk/zk_unknown_order.cpp",
]

target_scopes = [
    "Critical. A shipped API or protocol-peer path lets an attacker recover, forge, or substitute key shares, private scalars, Paillier secrets, TDH2 shares, or equivalent secret material.",
    "Critical. A single malicious peer or below-threshold coalition obtains a valid ECDSA, EdDSA, Schnorr, HD-derived key, PVE recovery, or TDH2 decryption result without the required honest participants.",
    "High. Attacker-controlled blobs, proofs, ciphertexts, points, scalars, party names, access structures, labels, or session data are accepted under the wrong curve, key, party set, label, transcript, or protocol version.",
    "High. Public API reachable validation bypass in DKG, refresh, signing, PVE, TDH2, ZK proofs, commitments, OT, Paillier, ElGamal, or access-structure reconstruction creates accepted invalid cryptographic output.",
    "Medium. Public API reachable parsing, serialization, memory, quorum, or transport-message invariant break causes honest-party divergence, unsafe state acceptance, or invalid cryptographic output with security impact.",
]


def question_generator(target_file: str) -> str:
    """
    Generate exploit-focused audit + fuzzing questions for one cb-mpc target.

    ```
    target_file format:
    "'File Name: src/cbmpc/protocol/ecdsa_mp.cpp -> Scope: Critical. A single malicious peer or below-threshold coalition produces a valid signature without the required participants.'"
    ```
    """

    prompt = f"""
    ```
    Generate exploit-focused security audit and fuzzing questions for this exact Coinbase cb-mpc target:

    {target_file}

    Project context: cb-mpc is a C++ MPC cryptography library for DKG, refresh, ECDSA/EdDSA/Schnorr signing, HD-MPC derivation, PVE backup/recovery, TDH2 threshold decryption, ZK proofs, commitments, Paillier/ElGamal/RSA/ECC primitives, access structures, and C/C++ API wrappers.

    Rules:
    * Treat `File Name:` as the exact file/module and `Scope:` as the only impact to target.
    * Assume full repo context. Do not ask for code or say files are missing.
    * Attacker is an unprivileged API caller, malicious serialized-input provider, malicious transport peer, malicious callback provider, or Byzantine participant below threshold.
    * Do not rely on threshold-or-higher collusion, leaked keys, insecure app policy, unauthenticated transport as the only cause, deployment mistakes, physical compromise, social engineering, raw DoS, unbounded allocation/growth/loop/time/resource exhaustion, RNG-quality complaints, or zeroization-only issues.
    * Ignore tests, docs, scripts, demos, examples, mocks, benchmarks, vendors, build files, local tooling, and out-of-scope feature-only code.
    * Generate 20 to 30 high-signal questions; every question must name the attacker-controlled input, the suspected missing/insufficient guard, and the accepted bad output.
    * At least 70% should chain two layers, such as API wrapper -> parser, transport -> transcript, proof verify -> protocol output, PVE verify -> decrypt/combine, access structure -> reconstruction, or DKG/refresh -> signing.
    * Prefer bug classes that are realistic in this codebase: blob version/type confusion, wrong curve/key/label binding, malformed point/scalar acceptance, non-canonical DER/compressed/x-only encodings, session or aux reuse, proof/ciphertext verification bypass, quorum-name drift, access-structure ambiguity, C/C++ wrapper lifetime/length mismatch, unchecked callback output, or uniform/non-uniform message mix-up.
    * Avoid generic "is X validated" questions. Ask only if there is a plausible path from attacker bytes/messages to a valid-looking signature, key blob, ciphertext, proof, decrypted scalar, reconstructed share, or accepted protocol state.
    * Every question must be locally testable by unit/integration/fuzz test, deterministic fake transport, multi-party simulation, or differential/model comparison.

    Smart hunting strategy: look for boundary mismatches where one layer validates weaker data than the next layer assumes; places where `mem_t` length, curve id, party name, label, `sid`, aux, public share, access-structure leaf, proof flag, or blob version is parsed once and trusted later; and flows where an error, empty output, or receiver-only output can be confused with success.

    File-aware focus: for `api`/`c_api`, target wrapper validation and type/lifetime/length conversion; for `protocol`, target malicious peer messages, transcript binding, quorum logic, and output acceptance; for `crypto`/`zk`, target public-flow reachability into unchecked primitive assumptions; for `core`, target serialization, buffer, conversion, and error semantics.

    High-value surfaces: public API wrappers, C API adapters, `mem_t`/`buf_t`/`converter_t`, key/keyset blobs, job transport, DKG/refresh/signing, HD derivation, TDH2, PVE, access structures, secret sharing, OT, ZK proofs, commitments, Paillier, ElGamal, RSA-OAEP, ECC point/scalar encoding, and error propagation.

    Each question must include target function/module, attacker action, preconditions, call sequence, invariant tested, scoped impact, and proof idea.

    Output only valid Python. No markdown. No explanations.

    questions = [
    "[File: {target_file}] [Function: symbol_or_module] Can an unprivileged ATTACKER_ACTION controlling INPUT_BYTES_OR_MESSAGES under PRECONDITIONS trigger CALL_SEQUENCE, bypassing EXPECTED_GUARD and causing MODULE_A and MODULE_B to disagree about INVARIANT, so honest code accepts BAD_OUTPUT with scoped impact: SCOPE_IMPACT? Proof idea: build a deterministic unit/integration/fuzz/state test that drives PARAMETERS, models attacker-controlled bytes/messages/order, and asserts EXPECTED_REJECTION_OR_INVARIANT.",
    ]
    """
    return prompt


def audit_format(question: str) -> str:
    """
    Generate a focused cb-mpc exploit-question validation prompt.
    """
    return f"""# QUESTION SCAN PROMPT

## Exploit Question
{question}

## Scope
Audit production cb-mpc code reachable from supported C/C++ APIs or protocol-peer boundaries: `include/`, `include-internal/`, and `src/`. Ignore tests, docs, demos, examples, mocks, benchmarks, vendors, scripts, build files, local tooling, and pure integration misuse.

## Objective
Decide whether the question describes a real reachable vulnerability. The attacker must be an unprivileged API caller, malicious serialized-input provider, malicious transport peer, malicious callback provider, or Byzantine participant below threshold. Prefer #NoVulnerability unless there is a concrete accepted-bad-output path.

## Allowed Impact Scope
- Critical. A shipped API or protocol-peer path lets an attacker recover, forge, or substitute key shares, private scalars, Paillier secrets, TDH2 shares, or equivalent secret material.
- Critical. A single malicious peer or below-threshold coalition obtains a valid ECDSA, EdDSA, Schnorr, HD-derived key, PVE recovery, or TDH2 decryption result without the required honest participants.
- High. Attacker-controlled blobs, proofs, ciphertexts, points, scalars, party names, access structures, labels, or session data are accepted under the wrong curve, key, party set, label, transcript, or protocol version.
- High. Public API reachable validation bypass in DKG, refresh, signing, PVE, TDH2, ZK proofs, commitments, OT, Paillier, ElGamal, or access-structure reconstruction creates accepted invalid cryptographic output.
- Medium. Public API reachable parsing, serialization, memory, quorum, or transport-message invariant break causes honest-party divergence, unsafe state acceptance, or invalid cryptographic output with security impact.

## Method
Trace entrypoint -> attacker-controlled bytes/messages -> exact files/functions -> expected guard -> why guard is absent/insufficient -> accepted bad output -> concrete impact. Check both wrapper-level validation and internal assumptions. Reject if existing validation, documented caller responsibility, or threshold assumptions prevent it.

## Reject Immediately
Threshold-or-above collusion, leaked keys, compromised host memory, insecure app policy, unauthenticated transport as the only cause, deployment error, physical/social attack, raw DoS, unbounded allocation/growth/loop/time/resource exhaustion, tests/docs/demos/vendors only, external dependency only, RNG-quality complaint, zeroization-only issue, harmless reject, crash-only issue, or no concrete scoped impact.

## Output
If valid, output:

### Title
[Clear vulnerability statement] - ([File: file_path])

### Summary
### Finding Description
### Impact Explanation
### Likelihood Explanation
### Recommendation
### Proof of Concept

If invalid, output exactly:
#NoVulnerability found for this question.
"""


def scan_format(report: str) -> str:
    """
    Generate a short cross-project analog scan prompt for cb-mpc.
    """
    prompt = f"""# ANALOG SCAN PROMPT

## External Report
{report}

## Objective
Use the report's bug class as a hint to find a real cb-mpc analog in production C/C++ library code. Only report if cb-mpc has its own reachable root cause, not just a similar-looking component.

## Allowed Impact Scope
- Critical. A shipped API or protocol-peer path lets an attacker recover, forge, or substitute key shares, private scalars, Paillier secrets, TDH2 shares, or equivalent secret material.
- Critical. A single malicious peer or below-threshold coalition obtains a valid ECDSA, EdDSA, Schnorr, HD-derived key, PVE recovery, or TDH2 decryption result without the required honest participants.
- High. Attacker-controlled blobs, proofs, ciphertexts, points, scalars, party names, access structures, labels, or session data are accepted under the wrong curve, key, party set, label, transcript, or protocol version.
- High. Public API reachable validation bypass in DKG, refresh, signing, PVE, TDH2, ZK proofs, commitments, OT, Paillier, ElGamal, or access-structure reconstruction creates accepted invalid cryptographic output.
- Medium. Public API reachable parsing, serialization, memory, quorum, or transport-message invariant break causes honest-party divergence, unsafe state acceptance, or invalid cryptographic output with security impact.

## Method
Classify the bug class, map it to exact cb-mpc files/functions, prove the supported API/protocol entry path, identify the first bad trust transition, explain the accepted bad output, and reject if validation, threshold assumptions, or documented caller responsibilities prevent it.

## Disqualify
No supported API/protocol entry, threshold collusion, leaked keys, insecure deployment, unauthenticated transport only, tests/docs/demos/vendors only, external dependency only, theoretical-only, raw DoS, unbounded allocation/growth/loop/time/resource exhaustion, crash-only, observability-only, or missing impact.

## Output (Strict)
If valid analog exists, output:

### Title
[Clear vulnerability statement] - ([File: file_path])

### Summary
### Finding Description
### Impact Explanation
### Likelihood Explanation
### Recommendation
### Proof of Concept

If not, output exactly:
#NoVulnerability found for this question.

No extra text.
"""
    return prompt


def validation_format(report: str) -> str:
    """
    Generate a strict cb-mpc bounty-style validation prompt for security claims.
    """
    prompt = f"""# VALIDATION PROMPT

## Security Claim
{report}

## Rules
Validate only this claim. The issue must be reachable through cb-mpc production C/C++ API code or the protocol-peer boundary, with exact file/function references and a reproducible local proof. Do not invent a stronger issue or upgrade severity beyond the evidence.

Reject admin/trusted-operator-only, threshold-or-above-collusion, leaked-key, deployment-only, docs/style/config/build-only, tests/demos/vendors-only, pure integration misuse, physical/social attack, unauthenticated transport only, raw DoS, unbounded allocation/growth/loop/time/resource exhaustion, RNG-quality complaint, zeroization-only, and theoretical-only claims.

## In-Scope Areas
Public C++ APIs, C API adapters, job transport, DKG, refresh, signing, HD keysets, TDH2, PVE, access structures, key/keyset blobs, secret sharing, OT, ZK proofs, commitments, Paillier, ElGamal, RSA-OAEP, ECC validation, `mem_t`/`buf_t`/`converter_t`, serialization, and error propagation.

## Allowed Impact Scope
- Critical. A shipped API or protocol-peer path lets an attacker recover, forge, or substitute key shares, private scalars, Paillier secrets, TDH2 shares, or equivalent secret material.
- Critical. A single malicious peer or below-threshold coalition obtains a valid ECDSA, EdDSA, Schnorr, HD-derived key, PVE recovery, or TDH2 decryption result without the required honest participants.
- High. Attacker-controlled blobs, proofs, ciphertexts, points, scalars, party names, access structures, labels, or session data are accepted under the wrong curve, key, party set, label, transcript, or protocol version.
- High. Public API reachable validation bypass in DKG, refresh, signing, PVE, TDH2, ZK proofs, commitments, OT, Paillier, ElGamal, or access-structure reconstruction creates accepted invalid cryptographic output.
- Medium. Public API reachable parsing, serialization, memory, quorum, or transport-message invariant break causes honest-party divergence, unsafe state acceptance, or invalid cryptographic output with security impact.

## Required Checks
Confirm supported entrypoint, attacker-controlled data, first bad trust transition, existing guard failure, exploit sequence, accepted bad output, concrete impact, likelihood, and a safe PoC plan. Prefer #NoVulnerability for speculative, untestable, or caller-responsibility issues.

## Output
If valid, output exactly:

Audit Report

## Title
[Clear vulnerability statement] - ([File: file_path])

## Summary
[2-3 sentence summary of the bug and impact]

## Finding Description
[Exact code path, root cause, exploit flow, and why existing checks fail]

## Impact Explanation
[Concrete allowed cb-mpc impact and severity rationale]

## Likelihood Explanation
[Attacker capability, required conditions, feasibility, repeatability]

## Recommendation
[Specific fix guidance]

## Proof of Concept
[Minimal reproducible steps or fuzz/invariant/simulation test plan]

If invalid, output exactly:
#NoVulnerability found for this question.

Output only one of the two outcomes above. No extra text.
"""
    return prompt
