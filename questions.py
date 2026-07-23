import json
import os

from decouple import config

# todo: if scope_files is: 500 > 50, 300 > 30 , 100 > 10
MAX_REPO = 20
# todo: the path from https:///github.com/dfinity/ICRC-1
SOURCE_REPO = "Near-One/omni-bridge"
# todo: the name of the repository
REPO_NAME = "omni-bridge"
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
    "near/omni-bridge/src/btc.rs",
    "near/omni-bridge/src/lib.rs",
    "near/omni-bridge/src/migrate.rs",
    "near/omni-bridge/src/storage.rs",
    "near/omni-bridge/src/token_lock.rs",
    "near/omni-prover/evm-prover/src/lib.rs",
    "near/omni-prover/mpc-omni-prover/src/lib.rs",
    "near/omni-prover/wormhole-omni-prover-proxy/src/byte_utils.rs",
    "near/omni-prover/wormhole-omni-prover-proxy/src/lib.rs",
    "near/omni-prover/wormhole-omni-prover-proxy/src/parsed_vaa.rs",
    "near/omni-token/src/lib.rs",
    "near/omni-token/src/migrate.rs",
    "near/omni-token/src/omni_ft.rs",
    "near/omni-types/src/bounded_string.rs",
    "near/omni-types/src/btc.rs",
    "near/omni-types/src/errors.rs",
    "near/omni-types/src/evm/events.rs",
    "near/omni-types/src/evm/header.rs",
    "near/omni-types/src/evm/mod.rs",
    "near/omni-types/src/evm/receipt.rs",
    "near/omni-types/src/hex_types.rs",
    "near/omni-types/src/lib.rs",
    "near/omni-types/src/locker_args.rs",
    "near/omni-types/src/mpc_types.rs",
    "near/omni-types/src/near_events.rs",
    "near/omni-types/src/prover_args.rs",
    "near/omni-types/src/prover_result.rs",
    "near/omni-types/src/sol_address.rs",
    "near/omni-types/src/starknet/events.rs",
    "near/omni-types/src/starknet/mod.rs",
    "near/omni-types/src/utils.rs",
    "near/token-deployer/src/lib.rs",
    "near/token-deployer/src/migrate.rs",
    "evm/src/common/Borsh.sol",
    "evm/src/common/IBridgeToken.sol",
    "evm/src/common/ICustomMinter.sol",
    "evm/src/eNear/contracts/ENearProxy.sol",
    "evm/src/eNear/contracts/IENear.sol",
    "evm/src/omni-bridge/contracts/BridgeToken.sol",
    "evm/src/omni-bridge/contracts/BridgeTypes.sol",
    "evm/src/omni-bridge/contracts/HlBridgeToken.sol",
    "evm/src/omni-bridge/contracts/OmniBridge.sol",
    "evm/src/omni-bridge/contracts/OmniBridgeWormhole.sol",
    "evm/src/omni-bridge/contracts/SelectivePausableUpgradable.sol",
    "solana/programs/bridge_token_factory/src/constants.rs",
    "solana/programs/bridge_token_factory/src/error.rs",
    "solana/programs/bridge_token_factory/src/instructions/admin/change_config.rs",
    "solana/programs/bridge_token_factory/src/instructions/admin/initialize.rs",
    "solana/programs/bridge_token_factory/src/instructions/admin/mod.rs",
    "solana/programs/bridge_token_factory/src/instructions/admin/pause.rs",
    "solana/programs/bridge_token_factory/src/instructions/admin/update_metadata.rs",
    "solana/programs/bridge_token_factory/src/instructions/mod.rs",
    "solana/programs/bridge_token_factory/src/instructions/user/deploy_token.rs",
    "solana/programs/bridge_token_factory/src/instructions/user/finalize_transfer.rs",
    "solana/programs/bridge_token_factory/src/instructions/user/finalize_transfer_sol.rs",
    "solana/programs/bridge_token_factory/src/instructions/user/get_version.rs",
    "solana/programs/bridge_token_factory/src/instructions/user/init_transfer.rs",
    "solana/programs/bridge_token_factory/src/instructions/user/init_transfer_sol.rs",
    "solana/programs/bridge_token_factory/src/instructions/user/log_metadata.rs",
    "solana/programs/bridge_token_factory/src/instructions/user/mod.rs",
    "solana/programs/bridge_token_factory/src/instructions/wormhole_cpi.rs",
    "solana/programs/bridge_token_factory/src/lib.rs",
    "solana/programs/bridge_token_factory/src/state/config.rs",
    "solana/programs/bridge_token_factory/src/state/message/deploy_token.rs",
    "solana/programs/bridge_token_factory/src/state/message/finalize_transfer.rs",
    "solana/programs/bridge_token_factory/src/state/message/init_transfer.rs",
    "solana/programs/bridge_token_factory/src/state/message/log_metadata.rs",
    "solana/programs/bridge_token_factory/src/state/message/mod.rs",
    "solana/programs/bridge_token_factory/src/state/mod.rs",
    "solana/programs/bridge_token_factory/src/state/used_nonces.rs",
    "starknet/src/bridge_token.cairo",
    "starknet/src/bridge_types.cairo",
    "starknet/src/lib.cairo",
    "starknet/src/omni_bridge.cairo",
    "starknet/src/utils/borsh.cairo",
    "starknet/src/utils.cairo",
]

target_scopes = [
    "Critical. Unauthorized creation, release, withdrawal, or custody escape of native, locked, or wrapped bridge assets through settlement, deployment, or verification failure",
    "Critical. Irreversible fund lock, frozen redemption path, or permanently unclaimable user or protocol value in bridge, token, fee, vault, fast-transfer, or UTXO flows",
    "High. Replayable, non-unique, or duplicate cross-chain settlement across proof, event, nonce, message, or finalization domains that produces double-credit or unbacked supply",
    "High. Acceptance of forged, stale, cross-domain, malformed, differently-encoded, or insufficiently-bound proofs, signatures, VAAs, or prover outputs that bypass execution gates",
    "High. Asset-identity, token-mapping, decimals, fee-routing, refund, or balance-accounting divergence that breaks backing guarantees or sends value to the wrong party",
]



def question_generator(target_file: str) -> str:
    """
    Generate exploit-focused audit + fuzzing questions for one Omni Bridge production target.

    ```
    target_file format:
    "'File Name: near/omni-bridge/src/lib.rs -> Scope: Critical. Unauthorized creation, release, withdrawal, or custody escape of native, locked, or wrapped bridge assets through settlement, deployment, or verification failure'"
    ```
    """

    prompt = f"""
    ```

    Generate exploit-focused security audit and fuzzing questions for this exact Omni Bridge target:

    {target_file}

    Use live context from the project if available: NEAR omni-bridge settlement, fee, fast-transfer, BTC/UTXO, token-lock, and migration flows; omni-token mint/burn logic; token-deployer flows; omni-types payload serialization/parsing; EVM OmniBridge/OmniBridgeWormhole/BridgeToken/eNear contracts; Solana bridge_token_factory instruction/state/message flows; StarkNet omni_bridge and bridge_token flows; EVM/Wormhole/MPC prover verification; nonce tracking; finalised-transfer bookkeeping; decimal normalization; metadata logging; cross-chain token mapping; and cryptographic signature/proof validation.

    Protocol focus:
    This repository implements a multi-chain bridge between NEAR and foreign chains using MPC-signed outbound transfers and proof- or signature-verified inbound transfers. The audit focus is whether an unprivileged attacker can cause unauthorized transfer finalization, duplicate settlement, proof/signature replay, token deployment abuse, accounting drift, collateral mismatch, or permanent fund lock across NEAR, EVM, Solana, StarkNet, Wormhole-backed chains, and supported UTXO flows.

    Core invariants:

    * Transfers must settle at most once per legitimate origin event, nonce, or signed payload across every supported chain and prover path.
    * Funds locked, burned, minted, unlocked, or fee-routed on one chain must stay fully backed and correctly accounted for on the destination chain.
    * Proof verification, Wormhole/VAA parsing, MPC/ECDSA/eth-signature checks, and message serialization must reject forged, replayed, malformed, stale, cross-domain, or differently-encoded payloads.
    * Token deployment, metadata propagation, token mapping, and controller/locker interactions must not let attackers hijack canonical asset identity or mint unbacked representations.
    * Decimal normalization, fee handling, refunds, native-token wrapping, and fast-transfer or UTXO settlement logic must not leak value, strand funds, or create accounting drift outside documented behavior.

    Rules:

    * Treat `File Name:` as the exact file/module.
    * Treat `Scope:` as the ONLY impact to target.
    * Assume full repo context is accessible.
    * Do not ask for code or say anything is missing.
    * Attacker is strictly unprivileged: bridge user, token holder, contract caller, relayer submitting public proofs/messages, token deployer candidate, recipient string controller, or user controlling public cross-chain inputs.
    * Do not rely on malicious operators, guardians, colluding MPC threshold signers, leaked keys, privileged addresses, governance abuse, social engineering, front-run-only paths, network-level DoS, chain reorg assumptions, oracle-only failures, or public-mainnet testing.
    * Do not generate questions that depend only on known out-of-scope classes from SECURITY.md such as unbounded gas/storage consumption, griefing without asset/security impact, Wormhole guardian compromise, NEAR base-chain attacks, decimal dust from normalization, or intentional rejected-relayer stake forfeiture.
    * Do not generate self-harm or user-mistake-only scenarios: wrong recipient chosen by the sender, voluntary self-loss, bad configuration by the attacker against themselves, or flows where the user can only hurt their own funds without breaking protocol guarantees.
    * Generate 20 to 30 high-signal questions.
    * At least 70% must be multi-step flow, invariant, fuzz, accounting, replay, verifier, settlement, or cross-module questions.
    * Every question must be testable by PoC, unit test, fuzz test, invariant test, or differential test.
    * Avoid generic checklist questions and repeated root causes.
    * Every question must target a plausible valid issue.

    Investigation process:

    * Anchor on the exact target file/module, its public entrypoints, trust boundaries, and downstream state changes.
    * Generate questions from five distinct lenses so the audit path differs from a generic sweep:
      - uniqueness/finality failures;
      - verifier/binding failures;
      - asset/accounting failures;
      - state-machine/callback failures;
      - parsing/serialization failures.
    * Prefer questions that need at least two steps, two modules, or one valid-looking check that binds the wrong thing.
    * Remove paraphrases and keep only distinct root causes.
    * Bias toward counterexamples where an unprivileged attacker passes visible checks but still reaches an invalid state transition.

    High-value attack surfaces:

    * Settlement flows: `init_transfer`, `fin_transfer`, `deploy_token`, `log_metadata`, fee claim, fast transfer, native-token transfer, BTC/Zcash/UTXO settlement, and token lock/unlock paths.
    * Verification and replay boundaries: MPC signature validation, ECDSA recovery, Wormhole/VAA parsing, light-client or prover result handling, nonce/finalization bitmaps, chain-id/domain separation, and stale-proof handling.
    * Asset identity and accounting: token mappings, wrapped-token deployment, metadata propagation, decimals normalization, fee/native fee accounting, storage/refund accounting, and bridge-token mint/burn/unlock symmetry.
    * Cross-chain parsing and serialization: Borsh/ABI/Cairo/Anchor payload encoding, proof args/results, event/header parsing, receipt decoding, ByteArray/string/account/address conversion, and signer/recipient binding.
    * Upgrade, migration, and cross-module state: migration paths, token/controller updates, pause-gated flows, bridge factory state, used-nonce tracking, and inter-contract callbacks that can desynchronize custody or authorization.

    Impact mapping:

    * Critical: Unauthorized creation, release, withdrawal, or custody escape of native, locked, or wrapped bridge assets through settlement, deployment, or verification failure.
    * Critical: Irreversible fund lock, frozen redemption path, or permanently unclaimable user or protocol value in bridge, token, fee, vault, fast-transfer, or UTXO flows.
    * High: Replayable, non-unique, or duplicate cross-chain settlement across proof, event, nonce, message, or finalization domains that produces double-credit or unbacked supply.
    * High: Acceptance of forged, stale, cross-domain, malformed, differently-encoded, or insufficiently-bound proofs, signatures, VAAs, or prover outputs that bypass execution gates.
    * High: Asset-identity, token-mapping, decimals, fee-routing, refund, or balance-accounting divergence that breaks backing guarantees or sends value to the wrong party.

    Coverage requirements:

    * At least half of the questions must explicitly mention one of: nonce/finality uniqueness, proof binding, signer binding, token identity, decimals/fees, callback ordering, migration state, or parser ambiguity.
    * Prefer invariant tests, stateful fuzzing, differential parsing tests, or multi-call PoCs over one-call revert checks.

    Each question must include:

    1. target function/module;
    2. attacker action;
    3. preconditions;
    4. call sequence;
    5. invariant tested;
    6. scoped impact;
    7. proof idea.

    Output only valid Python. No markdown. No explanations.

    questions = [
    "[File: {target_file}] [Function: symbol_or_module] Can an unprivileged ATTACKER_ACTION under PRECONDITIONS trigger CALL_SEQUENCE, violating INVARIANT, causing scoped impact: SCOPE_IMPACT? Proof idea: fuzz/state-test PARAMETERS and assert EXPECTED_PROPERTY.",
    ]
    """
    return prompt


def audit_format(question: str) -> str:
    """
    Generate a focused Omni Bridge exploit-question validation prompt.
    """
    return f"""# QUESTION SCAN PROMPT

## Exploit Question
{question}

Focus only on production Omni Bridge code in `scope_files`, mainly:
- near/omni-bridge/src
- near/omni-prover/*/src
- near/omni-token/src
- near/omni-types/src
- near/token-deployer/src
- evm/src/common
- evm/src/eNear/contracts
- evm/src/omni-bridge/contracts
- solana/programs/bridge_token_factory/src
- starknet/src
Anything outside those production files is out of scope unless needed as direct supporting context.

## Rules
- Audit only production Omni Bridge code.
- Treat repo context as accessible. Do not ask for files or claim they are missing.
- Ignore tests, docs, mocks, e2e assets, scripts, configs, build files, IDE files, package metadata, vendored libraries, and local-only fixtures.
- The attacker must be strictly unprivileged and must enter through public bridge calls, token callbacks, proof/message submission, deploy/metadata flows, recipient-controlled inputs, or other public cross-chain inputs.
- Reject self-harm or user-mistake-only paths: wrong recipient chosen by the sender, voluntary self-loss, or cases where the attacker can only damage their own funds without violating protocol guarantees.
- Prefer #NoVulnerability unless the path is concrete, locally testable, and bounty-grade.

## Allowed Impact Scope
Only these impacts are valid:
- Critical. Unauthorized creation, release, withdrawal, or custody escape of native, locked, or wrapped bridge assets through settlement, deployment, or verification failure.
- Critical. Irreversible fund lock, frozen redemption path, or permanently unclaimable user or protocol value in bridge, token, fee, vault, fast-transfer, or UTXO flows.
- High. Replayable, non-unique, or duplicate cross-chain settlement across proof, event, nonce, message, or finalization domains that produces double-credit or unbacked supply.
- High. Acceptance of forged, stale, cross-domain, malformed, differently-encoded, or insufficiently-bound proofs, signatures, VAAs, or prover outputs that bypass execution gates.
- High. Asset-identity, token-mapping, decimals, fee-routing, refund, or balance-accounting divergence that breaks backing guarantees or sends value to the wrong party.

## Method
1. Trace the attacker-controlled entrypoint and exact production functions touched.
2. Check the binding or invariant being challenged: uniqueness/finality, verifier binding, token identity, accounting, callback order, or parsing.
3. Decide whether the exploit still works under current checks.
4. Prove root cause with exact file/function/line references.
5. Confirm exact scoped impact and realistic likelihood.

## Reject Immediately
- Requires a trusted role, leaked key, malicious operator behavior, colluding MPC threshold signers, compromised Wormhole guardians, privileged access, or external dependency compromise.
- Requires phishing, chain attacks, reorg assumptions, network-level DoS only, or public-mainnet testing.
- Only affects tests, docs, configs, scripts, mocks, fixtures, vendored code, or local deployment choices.
- Is self-harm-only, local misconfiguration, logging/observability noise, harmless revert, stale read, decimal dust, rejected relayer stake forfeiture, griefing without security impact, or theory without a concrete exploit path.

## Output
If valid:

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
    Generate a short cross-project analog scan prompt for Omni Bridge.
    """
    prompt = f"""# ANALOG SCAN PROMPT

## External Report
{report}

Focus only on production Omni Bridge code in `scope_files`, mainly:
- near/omni-bridge/src
- near/omni-prover/*/src
- near/omni-token/src
- near/omni-types/src
- near/token-deployer/src
- evm/src/common
- evm/src/eNear/contracts
- evm/src/omni-bridge/contracts
- solana/programs/bridge_token_factory/src
- starknet/src
Anything outside those production files is out of scope unless needed as direct supporting context.

## Rules
- Treat production Omni Bridge files as accessible context. Do not claim files are missing or inaccessible.
- Do not ask for repository contents.
- Do not scan tests, docs, build files, IDE files, configs, resources, local fixtures, vendored libraries, package metadata, or e2e assets as audited targets.
- Use the external report only as a hint. Report an analog only if Omni Bridge has its own reachable root cause.
- The attacker must be strictly unprivileged and must enter through public protocol inputs.
- Reject self-harm or user-mistake-only paths.

## Allowed Impact Scope
Only these impacts are valid:
- Critical. Unauthorized creation, release, withdrawal, or custody escape of native, locked, or wrapped bridge assets through settlement, deployment, or verification failure.
- Critical. Irreversible fund lock, frozen redemption path, or permanently unclaimable user or protocol value in bridge, token, fee, vault, fast-transfer, or UTXO flows.
- High. Replayable, non-unique, or duplicate cross-chain settlement across proof, event, nonce, message, or finalization domains that produces double-credit or unbacked supply.
- High. Acceptance of forged, stale, cross-domain, malformed, differently-encoded, or insufficiently-bound proofs, signatures, VAAs, or prover outputs that bypass execution gates.
- High. Asset-identity, token-mapping, decimals, fee-routing, refund, or balance-accounting divergence that breaks backing guarantees or sends value to the wrong party.

## Method
1. Classify the external bug class: uniqueness/finality, verifier binding, asset/accounting, state machine, or parsing.
2. Map that class to exact Omni Bridge production files and attacker-controlled entrypoints.
3. Prove Omni Bridge has its own root cause with exact file/function/line references.
4. Confirm exact scoped impact and realistic likelihood.

## Disqualify Immediately
- No reachable attacker-controlled entry path.
- Requires trusted role, leaked key, malicious operator behavior, colluding MPC threshold signers, compromised Wormhole guardians, privileged access, or external dependency compromise.
- Requires phishing, public-mainnet testing, chain attack assumptions, or network-level DoS only.
- Is test/docs/config/build-only, self-harm-only, theoretical-only, or has no matching in-scope impact.
- Impact is only local misconfiguration, observability/logging noise, harmless revert, stale read, decimal dust, rejected relayer stake forfeiture, or non-security correctness.

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
    Generate a strict Omni Bridge bounty-style validation prompt for security claims.
    """
    prompt = f"""# VALIDATION PROMPT

## Security Claim
{report}

Focus only on production Omni Bridge code in `scope_files`, mainly:
- near/omni-bridge/src
- near/omni-prover/*/src
- near/omni-token/src
- near/omni-types/src
- near/token-deployer/src
- evm/src/common
- evm/src/eNear/contracts
- evm/src/omni-bridge/contracts
- solana/programs/bridge_token_factory/src
- starknet/src
Anything outside those production files is out of scope unless needed as direct supporting context.

## Rules
- Validate only the submitted claim.
- Check SECURITY.md, Researcher.md if present, and the bounty scope for exclusions and valid impact classes.
- Do not create a new issue if the claim is weak.
- Do not upgrade severity unless the evidence proves it.
- The exploit must be triggerable by a strictly unprivileged user through public bridge/proof/message/deploy/metadata/token-callback flows, unless the claim proves privilege escalation from such a path.
- Reject self-harm or user-mistake-only scenarios.
- Reject malicious-operator-only, privileged-only, leaked-key, colluding-threshold, Wormhole-guardian-compromise, host-compromise, best-practice, docs/style, config/test-only, gas-only, front-run-only, network-level-DoS-only, and purely theoretical claims.
- Reject assumptions that require phishing, governance/51% control, third-party compromise, unsupported protocol behavior, or NEAR base-chain attacks.
- Prefer #NoVulnerability over speculation.

## In-Scope Areas
- NEAR bridge flows: settlement, fee, fast-transfer, token lock, BTC/UTXO, migration, and callbacks.
- Prover/verifier flows: EVM prover, Wormhole proxy, MPC prover, signature recovery, domain separation, proof result handling.
- Token/asset flows: omni-token, bridge-token deployment, token-deployer, metadata propagation, token mapping, wrapped/native custody, decimals, mint/burn/unlock symmetry.
- Foreign-chain bridge logic: EVM OmniBridge/OmniBridgeWormhole/eNear, Solana `bridge_token_factory`, StarkNet `omni_bridge`, including nonce/finalization/deploy/init/finalize handlers.
- Shared parsing/types: omni-types, payload shaping, event/header/receipt parsing, and address/string conversion used in production settlement paths.
- Reject third-party dapps, websites, tests, docs, examples, mocks, generated files, local deployment helpers, vendored libraries, e2e tooling, and local developer tooling unless the claim proves direct in-scope protocol impact.

## Allowed Impact Scope
Only these impacts are valid:
- Critical. Unauthorized creation, release, withdrawal, or custody escape of native, locked, or wrapped bridge assets through settlement, deployment, or verification failure.
- Critical. Irreversible fund lock, frozen redemption path, or permanently unclaimable user or protocol value in bridge, token, fee, vault, fast-transfer, or UTXO flows.
- High. Replayable, non-unique, or duplicate cross-chain settlement across proof, event, nonce, message, or finalization domains that produces double-credit or unbacked supply.
- High. Acceptance of forged, stale, cross-domain, malformed, differently-encoded, or insufficiently-bound proofs, signatures, VAAs, or prover outputs that bypass execution gates.
- High. Asset-identity, token-mapping, decimals, fee-routing, refund, or balance-accounting divergence that breaks backing guarantees or sends value to the wrong party.

Anything limited to observability, non-security correctness, harmless revert/reject, stale read, local misconfiguration, self-loss without protocol break, or non-demonstrable exploitation is invalid.

## Required Validation Checks
All must pass:
1. Exact in-scope file, function, and line/code references.
2. Clear root cause and broken security/accounting/binding assumption.
3. Reachable exploit path: preconditions -> attacker action -> trigger -> bad result.
4. Existing checks reviewed and shown insufficient.
5. Concrete allowed impact with realistic likelihood.
6. Reproducible proof path: PoC, integration test, invariant/fuzz test, differential test, or exact local steps.
7. No obvious exclusion, privilege requirement, or self-harm-only framing.

## Silent Triage Questions
Before output, internally answer:
- Can a normal unprivileged external user trigger this through a public Omni Bridge path?
- Does the code actually behave as claimed?
- Is the impact caused by Omni Bridge production code, not an external dependency alone?
- Is the impact concrete, in-scope, and not just self-loss or theory?
- Would a bounty triager accept the proof, and what exact test proves it?

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
[Concrete allowed Omni Bridge bounty impact and severity rationale]

## Likelihood Explanation
[Attacker capability, required conditions, feasibility, repeatability]

## Recommendation
[Specific fix guidance]

## Proof of Concept
[Minimal reproducible steps or fuzz/invariant/fork test plan]

If invalid, output exactly:
#NoVulnerability found for this question.

Output only one of the two outcomes above. No extra text.
"""
    return prompt
