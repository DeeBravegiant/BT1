import json
import os

# todo: if scope_files is: 500 > 50, 300 > 30 , 100 > 10
MAX_REPO = 25
SOURCE_REPO = "starkware-libs/sequencer"
REPO_NAME = "sequencer"
run_number = os.environ.get("GITHUB_RUN_NUMBER") or os.environ.get(
    "CI_PIPELINE_IID", "0"
)


def get_cyclic_index(run_number, max_index=100):
    """Convert run number to a cyclic index between 1 and max_index."""
    return (int(run_number) - 1) % max_index + 1


def load_repository_urls():
    """Load repository URLs from repositories.json."""
    repo_file = os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "repositories.json"
    )
    if not os.path.exists(repo_file):
        return []

    try:
        with open(repo_file, "r", encoding="utf-8") as f:
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
    "crates/apollo_batcher/src/batcher.rs",
    "crates/apollo_batcher/src/block_builder.rs",
    "crates/apollo_batcher/src/commitment_manager/commitment_manager_impl.rs",
    "crates/apollo_batcher/src/commitment_manager/state_committer.rs",
    "crates/apollo_batcher/src/pre_confirmed_block_writer.rs",
    "crates/apollo_batcher/src/pre_confirmed_cende_client.rs",
    "crates/apollo_batcher/src/transaction_executor.rs",
    "crates/apollo_batcher/src/transaction_provider.rs",
    "crates/apollo_batcher_types/src/batcher_types.rs",
    "crates/apollo_consensus/src/manager.rs",
    "crates/apollo_consensus/src/single_height_consensus.rs",
    "crates/apollo_consensus/src/state_machine.rs",
    "crates/apollo_consensus/src/storage.rs",
    "crates/apollo_consensus/src/stream_handler.rs",
    "crates/apollo_consensus/src/types.rs",
    "crates/apollo_consensus/src/votes_threshold.rs",
    "crates/apollo_consensus_manager/src/consensus_manager.rs",
    "crates/apollo_consensus_orchestrator/src/build_proposal.rs",
    "crates/apollo_consensus_orchestrator/src/cende/central_objects.rs",
    "crates/apollo_consensus_orchestrator/src/cende/mod.rs",
    "crates/apollo_consensus_orchestrator/src/fee_market/mod.rs",
    "crates/apollo_consensus_orchestrator/src/orchestrator_versioned_constants.rs",
    "crates/apollo_consensus_orchestrator/src/sequencer_consensus_context.rs",
    "crates/apollo_consensus_orchestrator/src/validate_proposal.rs",
    "crates/apollo_gateway/src/gateway.rs",
    "crates/apollo_gateway/src/state_reader.rs",
    "crates/apollo_gateway/src/stateful_transaction_validator.rs",
    "crates/apollo_gateway/src/stateless_transaction_validator.rs",
    "crates/apollo_gateway/src/sync_state_reader.rs",
    "crates/apollo_gateway_types/src/gateway_types.rs",
    "crates/apollo_http_server/src/deprecated_gateway_transaction.rs",
    "crates/apollo_http_server/src/http_server.rs",
    "crates/apollo_l1_provider/src/catchupper.rs",
    "crates/apollo_l1_provider/src/l1_provider.rs",
    "crates/apollo_l1_provider/src/l1_scraper.rs",
    "crates/apollo_l1_provider/src/transaction_manager.rs",
    "crates/apollo_l1_provider/src/transaction_record.rs",
    "crates/apollo_mempool/src/fee_transaction_queue.rs",
    "crates/apollo_mempool/src/fifo_transaction_queue.rs",
    "crates/apollo_mempool/src/mempool.rs",
    "crates/apollo_mempool/src/transaction_pool.rs",
    "crates/apollo_mempool_p2p/src/propagator/mod.rs",
    "crates/apollo_mempool_p2p/src/runner/mod.rs",
    "crates/apollo_network/src/authentication/negotiator.rs",
    "crates/apollo_network/src/gossipsub_impl.rs",
    "crates/apollo_network/src/misconduct_score.rs",
    "crates/apollo_network/src/mixed_behaviour.rs",
    "crates/apollo_network/src/network_manager/mod.rs",
    "crates/apollo_network/src/peer_manager/behaviour_impl.rs",
    "crates/apollo_network/src/peer_manager/peer.rs",
    "crates/apollo_network/src/sqmr/behaviour.rs",
    "crates/apollo_network/src/sqmr/handler.rs",
    "crates/apollo_network/src/sqmr/handler/inbound_session.rs",
    "crates/apollo_network/src/sqmr/messages.rs",
    "crates/apollo_network/src/sqmr/protocol.rs",
    "crates/apollo_node/src/clients.rs",
    "crates/apollo_node/src/components.rs",
    "crates/apollo_node/src/main.rs",
    "crates/apollo_node/src/servers.rs",
    "crates/apollo_p2p_sync/src/client/block_data_stream_builder.rs",
    "crates/apollo_p2p_sync/src/client/class.rs",
    "crates/apollo_p2p_sync/src/client/header.rs",
    "crates/apollo_p2p_sync/src/client/state_diff.rs",
    "crates/apollo_p2p_sync/src/client/transaction.rs",
    "crates/apollo_p2p_sync/src/server/mod.rs",
    "crates/apollo_p2p_sync/src/server/utils.rs",
    "crates/apollo_protobuf/src/codec.rs",
    "crates/apollo_protobuf/src/consensus.rs",
    "crates/apollo_protobuf/src/converters/consensus.rs",
    "crates/apollo_protobuf/src/converters/header.rs",
    "crates/apollo_protobuf/src/converters/state_diff.rs",
    "crates/apollo_protobuf/src/converters/transaction.rs",
    "crates/apollo_protobuf/src/mempool.rs",
    "crates/apollo_protobuf/src/sync.rs",
    "crates/apollo_rpc/src/api.rs",
    "crates/apollo_rpc/src/pending.rs",
    "crates/apollo_rpc/src/syncing_state.rs",
    "crates/apollo_rpc/src/v0_8/api/api_impl.rs",
    "crates/apollo_rpc/src/v0_8/broadcasted_transaction.rs",
    "crates/apollo_rpc/src/v0_8/transaction.rs",
    "crates/apollo_state_sync/src/runner/mod.rs",
    "crates/apollo_storage/src/base_layer.rs",
    "crates/apollo_storage/src/block_hash.rs",
    "crates/apollo_storage/src/consensus.rs",
    "crates/apollo_storage/src/global_root.rs",
    "crates/apollo_storage/src/header.rs",
    "crates/apollo_storage/src/state/data.rs",
    "crates/blockifier/src/blockifier.rs",
    "crates/blockifier/src/blockifier/block.rs",
    "crates/blockifier/src/blockifier/stateful_validator.rs",
    "crates/blockifier/src/blockifier/transaction_executor.rs",
    "crates/blockifier/src/context.rs",
    "crates/blockifier/src/transaction/account_transaction.rs",
    "crates/blockifier/src/transaction/l1_handler_transaction.rs",
    "crates/blockifier/src/transaction/transaction_execution.rs",
    "crates/shared_execution_objects/src/central_objects.rs",
    "crates/starknet_api/src/block.rs",
    "crates/starknet_api/src/block_hash.rs",
    "crates/starknet_api/src/block_hash/block_hash_calculator.rs",
    "crates/starknet_api/src/block_hash/event_commitment.rs",
    "crates/starknet_api/src/block_hash/receipt_commitment.rs",
    "crates/starknet_api/src/block_hash/state_diff_hash.rs",
    "crates/starknet_api/src/block_hash/transaction_commitment.rs",
    "crates/starknet_api/src/consensus_transaction.rs",
    "crates/starknet_api/src/data_availability.rs",
    "crates/starknet_api/src/executable_transaction.rs",
    "crates/starknet_api/src/state.rs",
    "crates/starknet_api/src/transaction.rs",
    "crates/starknet_api/src/transaction_hash.rs",
    "crates/starknet_api/src/versioned_constants_logic.rs",
]

target_scopes = [
    "Critical. Unprivileged-user-triggered consensus proposal, vote, certificate, height/round, or validator-set bug makes honest sequencer nodes decide, commit, or sync different Starknet block histories.",
    "Critical. Unprivileged-user-triggered proposal validation, batch construction, block hash, state diff, commitment, or storage update bug lets an invalid block be accepted or a valid block be rejected by honest nodes.",
    "Critical. Unprivileged-user-triggered transaction execution, class declaration, L1 handler, fee market, gas price, or block context mismatch produces a different post-state root, block hash, receipt, event, fee, or message commitment for the same accepted input.",
    "Critical. Unprivileged-user-triggered L1 provider, central sync, state sync, or p2p sync path causes a node to anchor to the wrong L1 event, block number, state diff, class, transaction, or finality view.",
    "High. Unprivileged-user-triggered mempool, gateway, RPC, or p2p transaction path admits transactions that proposal validation or canonical execution must reject, or drops valid transactions for protocol-invalid reasons.",
    "High. Unprivileged-user-triggered network, SQMR, gossipsub, protobuf, or peer authentication bug forges, replays, misattributes, or routes consensus/sync/mempool data with chain-safety consequences.",
    "High. Unprivileged-user-triggered storage, header, global root, pending block, or syncing-state bug serves stale, inconsistent, or cross-block data as authoritative to RPC, sync, or proposal code.",
]

EXECUTION_ALLOWED_IMPACT_SCOPE = """## Allowed Impact Scope
Only these impacts are valid:
- Critical. Invalid or unauthorized Starknet transaction accepted through account validation, signature, nonce, chain id, fee/resource bound, paymaster, or account-deployment logic.
- Critical. Wrong state, receipt, event, L1 message, class hash, storage value, or revert result from blockifier/syscall/execution logic for accepted input.
- Critical. Incorrect fee, gas, bouncer, resource accounting, refund, balance, or L1 gas price effect with economic impact.
- Critical. Wrong compiled class, CASM/native artifact, class hash, or contract code selected for execution.
- High. Mempool/gateway/RPC admission accepts invalid transactions or rejects valid transactions before sequencing.
- High. RPC execution, fee estimation, tracing, simulation, or pending view returns an authoritative-looking wrong value.
- High. Transaction conversion or signature/hash logic binds the wrong signer, hash, type, or executable payload."""

SMART_AUDIT_PIVOTS = """## Sequencer-Specific Audit Pivots
- Proposal validation path: `validate_proposal` -> `is_block_info_valid` -> `initiate_validation` -> `handle_proposal_part` -> `BatcherClient::send_proposal_content` -> `ProposalFin` comparison. Attack questions should bind `ProposalInit.height/timestamp/l1_da_mode/l2_gas_price_fri/l1_gas_price_fri/l1_data_gas_price_fri`, `retrospective_block_hash`, `executed_transaction_count`, proof verification tasks, and `ProposalCommitment`.
- Block building path: `BlockBuilder::build_block_inner` -> `add_txs_to_executor` -> `handle_executed_txs` -> `close_block(final_n_executed_txs)` -> `BlockExecutionArtifacts::new`. Look for mismatches between started, executed, truncated, streamed, and committed transactions, not mere queue growth.
- Commitment path: `prepare_txs_hashing_data` -> `calculate_block_commitments` -> `PartialBlockHashComponents::new` -> `PartialBlockHash::from_partial_block_hash_components`. Bind transaction order, signatures, execution outputs, state diff length, L1 DA mode, gas prices, Starknet version, and final partial block hash.
- Admission-to-consensus bridge: `TransactionConverter` converts RPC/consensus/internal/executable transactions, computes transaction hashes with `chain_id`, verifies/stores proof facts, loads Sierra/executable classes, and maps L1 handlers. Look for cross-flow disagreement between gateway, mempool, batcher, validator, and consensus."""


def question_generator(target_file: str) -> str:
    """
    Generate exploit-focused audit and fuzzing questions for one Sequencer target.
    """

    prompt = f"""
    Generate exploit-focused security audit and fuzzing questions for this exact Starknet Sequencer target:

    {target_file}

    Project focus:
    This repository is `sequencer`, Starkware's Apollo/Starknet sequencer workspace. Focus on consensus and consensus-adjacent correctness: proposals, votes, validator sets, batch building, block hashes, state diffs, transaction execution, mempool/gateway admission, L1/L2 sync, p2p messages, protobuf boundaries, storage roots, and RPC views used by production node code.

    Execution/admission impact gate:
    {EXECUTION_ALLOWED_IMPACT_SCOPE}

    {SMART_AUDIT_PIVOTS}

    Rules:
    * Treat `File Name:` as the exact file/module and `Scope:` as the only impact.
    * Assume repo context is accessible; do not ask for code or claim files are missing.
    * Attacker must be unprivileged: public RPC client, ordinary account, contract deployer/caller, unauthenticated or low-trust peer, or sender of public L1/L2 data paths.
    * Do not grant validator, sequencer operator, block proposer, node admin, trusted central service, oracle, database, or deployment privileges unless the question proves an unprivileged bypass.
    * Malicious-peer-only behavior and malformed peer data are out of scope when the data is rejected, ignored, disconnected, retried, rate-limited, or only wastes resources.
    * Unbounded CPU/memory/disk/cache/queue growth, crashes, OOM, leaks, performance-only degradation, logging, metrics, tests, mocks, benches, generated data, scripts, and local tooling are not valid unless one allowed impact above is concretely reached.
    * Generate 18 to 24 high-signal questions; at least two thirds should cross modules or persisted state.
    * Anchor to concrete symbols, message types, storage columns, hashes, commitments, config values, transaction fields, or block/state identifiers.
    * Name the exact value at risk: decided block, proposal validity, state root, block hash, transaction hash, event/receipt commitment, class hash, nonce, fee, gas price, L1 handler message, validator set, sync cursor, storage row, RPC result, or peer identity.
    * Every question must be testable with a Rust unit/property/fuzz test, integration-style local reproducer, or deterministic protocol-state test.

    Output only valid Python. No markdown. No explanations.

    questions = [
    "[File: {target_file}] [Symbol: symbol_or_module] Can attacker-controlled INPUT under PRECONDITIONS reach CALL_PATH and violate INVARIANT, corrupting EXACT_VALUE with scoped impact SCOPE_IMPACT? Proof idea: build a Rust unit/property/fuzz/local reproducer over PARAMETERS and assert EXPECTED_PROTOCOL_PROPERTY.",
    ]
    """
    return prompt


def audit_format(question: str) -> str:
    """
    Generate a focused Sequencer exploit-question validation prompt.
    """
    return f"""# QUESTION SCAN PROMPT

## Exploit Question
{question}

## Scope Rules
- Audit only production Sequencer files listed in `scope_files`.
- Do not ask for repo contents or claim files are missing.
- Ignore tests, mocks, fixtures, generated data, docs, benches, metrics-only noise, scripts, deployment scaffolding, and local tooling.

## Objective
Decide whether the question leads to a real, reachable Sequencer vulnerability. The attacker must enter through public RPC/gateway transaction submission, transaction fields signed by their own keys, contract code/input, unauthenticated or low-trust p2p messages, public sync data, or L1/L2 data paths.

Reject claims needing validator, sequencer operator, block proposer, node admin, trusted central service, oracle, database, or deployment privileges unless the issue proves an unprivileged bypass. Prefer #NoVulnerability unless the path is concrete and proves High/Critical consensus, execution, admission, sync, storage, or RPC-authority impact.

## Required Execution/Admission Impacts
{EXECUTION_ALLOWED_IMPACT_SCOPE}

{SMART_AUDIT_PIVOTS}

## Method
1. Trace the attacker-controlled entrypoint.
2. Map it to exact production files and functions.
3. Check signatures, nonces, fees, class hashes, chain ids, block numbers, validator sets, vote thresholds, protobuf decoding, storage keys, L1 event ordering, sync cursors, and RPC admission guards.
4. Name the exact accepted or served value that becomes wrong.
5. Reject if existing validation already prevents the exploit.

## Reject Immediately
- Privileged operator/admin/validator/oracle/trusted-service assumptions.
- Malicious-peer-only or bad peer data that is rejected, ignored, disconnected, retried, rate-limited, or only wastes resources.
- Ordinary crash, DoS, performance, unbounded CPU/memory/disk/cache/queue growth, OOM, leaks, logs, style, or dependency-only behavior.
- Tests, mocks, benches, generated files, docs, scripts, deployments, or local tooling.
- No concrete unprivileged path or no exact corrupted block/state/admission/sync/RPC value.

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
    Generate a cross-project analog scan prompt for Sequencer protocol-safety issues.
    """
    prompt = f"""# ANALOG SCAN PROMPT

## External Report
{report}

## Task
Use the external report only as a bug-class seed. Search production `scope_files` for a Sequencer-native analog in consensus decisions, proposal validation, execution results, block/state commitments, mempool/gateway admission, p2p/sync routing, L1 anchoring, storage roots, or authoritative RPC views.

## Required Execution/Admission Impacts
{EXECUTION_ALLOWED_IMPACT_SCOPE}

{SMART_AUDIT_PIVOTS}

Report only if this repository has its own reachable root cause, unprivileged trigger, broken invariant, exact corrupted value, and matching target scope or one of the impacts above. Reject privileged operations, malicious-peer-only noise, resource-only issues, unbounded growth, dependency-only behavior, and anything outside production scope.

## Work Plan
1. Classify the external bug into one Sequencer invariant.
2. Map it to exact files/functions.
3. Trace attacker input through production validation.
4. Identify the wrong decided block, accepted proposal, state root, block hash, receipt/event commitment, class hash, nonce, fee, validator set, sync cursor, storage value, RPC result, or admission decision.
5. Reject if existing guards preserve the invariant.

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
    Generate a strict Sequencer validation prompt for security claims.
    """
    prompt = f"""# VALIDATION PROMPT

## Security Claim
{report}

## Rules
- Validate only the submitted claim against production Sequencer files in `scope_files`.
- Do not invent a stronger claim, change target scope, or upgrade severity without evidence.
- A valid issue must be triggered by an unprivileged RPC client, ordinary account, contract caller/deployer, low-trust peer, or public L1/L2 data path.
- Reject operator/admin/validator/proposer/oracle/trusted-service assumptions unless the report proves an unprivileged bypass.
- Reject malicious-peer-only behavior, bad data that is rejected/ignored/rate-limited, ordinary crash/DoS, unbounded CPU/memory/disk/cache/queue growth, OOM, leaks, logs, style, dependency-only bugs, tests, mocks, generated data, docs, scripts, deployments, and tooling.
- The final impact must exactly match one High/Critical `target_scopes` item or one execution/admission impact below, and identify the exact corrupted protocol-visible value.

## Required Execution/Admission Impacts
{EXECUTION_ALLOWED_IMPACT_SCOPE}

{SMART_AUDIT_PIVOTS}

## Required Checks
1. Exact file/function/line references.
2. Clear broken consensus, execution, admission, sync, storage, serialization, signature, or RPC-authority invariant.
3. Reachable exploit path: preconditions -> attacker input -> production call path -> bad value.
4. Existing guards reviewed and shown insufficient.
5. Exact wrong value named: decided block, proposal validity, state root, block hash, transaction hash, event/receipt commitment, class hash, nonce, fee, gas price, L1 message, validator set, sync cursor, storage row, RPC result, or peer identity.
6. Reproducible proof path: Rust unit/property/fuzz test, integration-style local reproducer, or deterministic protocol-state test.

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
[Concrete allowed repository impact and severity rationale]

## Likelihood Explanation
[Attacker capability, required conditions, feasibility, repeatability]

## Recommendation
[Specific fix guidance]

## Proof of Concept
[Minimal reproducible steps or test plan]

If invalid, output exactly:
#NoVulnerability found for this question.

Output only one of the two outcomes above. No extra text.
"""
    return prompt
