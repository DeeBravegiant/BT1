import json
import os

# todo: if scope_files is: 500 > 50, 300 > 30 , 100 > 10
MAX_REPO = 25
SOURCE_REPO = "near/nearcore"
REPO_NAME = "nearcore"
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
    "chain/chain/src/approval_verification.rs",
    "chain/chain/src/block_processing_utils.rs",
    "chain/chain/src/chain.rs",
    "chain/chain/src/chain_update.rs",
    "chain/chain/src/doomslug.rs",
    "chain/chain/src/lightclient.rs",
    "chain/chain/src/missing_chunks.rs",
    "chain/chain/src/orphan.rs",
    "chain/chain/src/pending.rs",
    "chain/chain/src/resharding/flat_storage_resharder.rs",
    "chain/chain/src/resharding/manager.rs",
    "chain/chain/src/resharding/migrations.rs",
    "chain/chain/src/resharding/resharding_actor.rs",
    "chain/chain/src/resharding/trie_state_resharder.rs",
    "chain/chain/src/runtime/mod.rs",
    "chain/chain/src/runtime/signer_overlay.rs",
    "chain/chain/src/runtime/trie_update_wrapper.rs",
    "chain/chain/src/sharding.rs",
    "chain/chain/src/signature_verification.rs",
    "chain/chain/src/spice/block_application.rs",
    "chain/chain/src/spice/chain.rs",
    "chain/chain/src/spice/chunk_application.rs",
    "chain/chain/src/spice/chunk_validation.rs",
    "chain/chain/src/spice/core.rs",
    "chain/chain/src/state_sync/adapter.rs",
    "chain/chain/src/state_sync/mod.rs",
    "chain/chain/src/state_sync/state_request_tracker.rs",
    "chain/chain/src/state_sync/utils.rs",
    "chain/chain/src/stateless_validation/chunk_endorsement.rs",
    "chain/chain/src/stateless_validation/chunk_validation.rs",
    "chain/chain/src/stateless_validation/processing_tracker.rs",
    "chain/chain/src/stateless_validation/state_witness.rs",
    "chain/chain/src/types.rs",
    "chain/chain/src/update_shard.rs",
    "chain/chain/src/validate.rs",
    "chain/chunks/src/chunk_cache.rs",
    "chain/chunks/src/client.rs",
    "chain/chunks/src/logic.rs",
    "chain/chunks/src/shards_manager_actor.rs",
    "chain/client/src/chunk_endorsement_handler.rs",
    "chain/client/src/chunk_inclusion_tracker.rs",
    "chain/client/src/chunk_producer.rs",
    "chain/client/src/client.rs",
    "chain/client/src/client_actor.rs",
    "chain/client/src/pending_transaction_queue.rs",
    "chain/client/src/prepare_transactions.rs",
    "chain/client/src/rpc_handler.rs",
    "chain/client/src/state_request_actor.rs",
    "chain/client/src/stateless_validation/chunk_endorsement.rs",
    "chain/client/src/stateless_validation/chunk_validation_actor.rs",
    "chain/client/src/stateless_validation/chunk_validator/mod.rs",
    "chain/client/src/stateless_validation/chunk_validator/orphan_witness_pool.rs",
    "chain/client/src/stateless_validation/partial_witness/encoding.rs",
    "chain/client/src/stateless_validation/partial_witness/partial_deploys_tracker.rs",
    "chain/client/src/stateless_validation/partial_witness/partial_witness_actor.rs",
    "chain/client/src/stateless_validation/partial_witness/partial_witness_tracker.rs",
    "chain/client/src/stateless_validation/shadow_validate.rs",
    "chain/client/src/stateless_validation/state_witness_producer.rs",
    "chain/client/src/stateless_validation/state_witness_tracker.rs",
    "chain/client/src/stateless_validation/validate.rs",
    "chain/client/src/sync/block.rs",
    "chain/client/src/sync/epoch.rs",
    "chain/client/src/sync/external.rs",
    "chain/client/src/sync/handler.rs",
    "chain/client/src/sync/header.rs",
    "chain/client/src/sync/state/chain_requests.rs",
    "chain/client/src/sync/state/downloader.rs",
    "chain/client/src/sync/state/mod.rs",
    "chain/client/src/sync/state/network.rs",
    "chain/client/src/sync/state/shard.rs",
    "chain/client/src/sync/state/task_tracker.rs",
    "chain/client/src/sync/state/util.rs",
    "chain/client/src/view_client_actor.rs",
    "chain/epoch-manager/src/epoch_info_aggregator.rs",
    "chain/epoch-manager/src/epoch_sync.rs",
    "chain/epoch-manager/src/lib.rs",
    "chain/epoch-manager/src/reward_calculator.rs",
    "chain/epoch-manager/src/shard_assignment/mod.rs",
    "chain/epoch-manager/src/shard_assignment/sticky_resharding.rs",
    "chain/epoch-manager/src/shard_tracker.rs",
    "chain/epoch-manager/src/validator_selection.rs",
    "chain/epoch-manager/src/validator_stats.rs",
    "chain/jsonrpc/src/api/blocks.rs",
    "chain/jsonrpc/src/api/call_function.rs",
    "chain/jsonrpc/src/api/chunks.rs",
    "chain/jsonrpc/src/api/light_client.rs",
    "chain/jsonrpc/src/api/query.rs",
    "chain/jsonrpc/src/api/status.rs",
    "chain/jsonrpc/src/api/transactions.rs",
    "chain/jsonrpc/src/api/validator.rs",
    "chain/jsonrpc/src/api/view_access_key.rs",
    "chain/jsonrpc/src/api/view_account.rs",
    "chain/jsonrpc/src/api/view_code.rs",
    "chain/jsonrpc/src/api/view_state.rs",
    "chain/jsonrpc/src/sharded_rpc.rs",
    "chain/network/src/accounts_data/mod.rs",
    "chain/network/src/announce_accounts/mod.rs",
    "chain/network/src/client.rs",
    "chain/network/src/network_protocol/edge.rs",
    "chain/network/src/network_protocol/mod.rs",
    "chain/network/src/network_protocol/peer.rs",
    "chain/network/src/network_protocol/state_sync.rs",
    "chain/network/src/peer/peer_actor.rs",
    "chain/network/src/peer_manager/peer_manager_actor.rs",
    "chain/network/src/routing/edge.rs",
    "chain/network/src/routing/graph/mod.rs",
    "chain/network/src/shards_manager.rs",
    "chain/network/src/state_sync.rs",
    "chain/network/src/state_witness.rs",
    "chain/network/src/types.rs",
    "chain/pool/src/lib.rs",
    "chain/pool/src/types.rs",
    "core/crypto/src/hash.rs",
    "core/crypto/src/hash_domain.rs",
    "core/crypto/src/signature.rs",
    "core/crypto/src/signer.rs",
    "core/crypto/src/vrf.rs",
    "core/primitives-core/src/account.rs",
    "core/primitives-core/src/apply.rs",
    "core/primitives-core/src/gas.rs",
    "core/primitives-core/src/hash.rs",
    "core/primitives-core/src/serialize.rs",
    "core/primitives-core/src/trie_key.rs",
    "core/primitives-core/src/types.rs",
    "core/primitives/src/action/mod.rs",
    "core/primitives/src/block.rs",
    "core/primitives/src/block_body.rs",
    "core/primitives/src/block_header.rs",
    "core/primitives/src/challenge.rs",
    "core/primitives/src/congestion_info.rs",
    "core/primitives/src/epoch_block_info.rs",
    "core/primitives/src/epoch_info.rs",
    "core/primitives/src/epoch_manager.rs",
    "core/primitives/src/epoch_sync.rs",
    "core/primitives/src/merkle.rs",
    "core/primitives/src/optimistic_block.rs",
    "core/primitives/src/receipt.rs",
    "core/primitives/src/reed_solomon.rs",
    "core/primitives/src/shard_layout/mod.rs",
    "core/primitives/src/shard_layout/v1.rs",
    "core/primitives/src/shard_layout/v2.rs",
    "core/primitives/src/shard_layout/v3.rs",
    "core/primitives/src/sharding.rs",
    "core/primitives/src/sharding/shard_chunk_header_inner.rs",
    "core/primitives/src/spice/chunk_endorsement.rs",
    "core/primitives/src/spice/partial_data.rs",
    "core/primitives/src/spice/state_witness.rs",
    "core/primitives/src/state.rs",
    "core/primitives/src/state_part.rs",
    "core/primitives/src/state_record.rs",
    "core/primitives/src/state_sync.rs",
    "core/primitives/src/stateless_validation/chunk_endorsement.rs",
    "core/primitives/src/stateless_validation/chunk_endorsements_bitmap.rs",
    "core/primitives/src/stateless_validation/contract_distribution.rs",
    "core/primitives/src/stateless_validation/partial_witness.rs",
    "core/primitives/src/stateless_validation/state_witness.rs",
    "core/primitives/src/stateless_validation/stored_chunk_state_transition_data.rs",
    "core/primitives/src/stateless_validation/validator_assignment.rs",
    "core/primitives/src/transaction.rs",
    "core/primitives/src/trie_key.rs",
    "core/primitives/src/trie_split.rs",
    "core/primitives/src/types.rs",
    "core/primitives/src/upgrade_schedule.rs",
    "core/primitives/src/validator_mandates/compute_price.rs",
    "core/primitives/src/validator_signer.rs",
    "core/store/src/adapter/chain_store.rs",
    "core/store/src/adapter/chunk_store.rs",
    "core/store/src/adapter/epoch_store.rs",
    "core/store/src/adapter/flat_store.rs",
    "core/store/src/adapter/trie_store.rs",
    "core/store/src/flat/delta.rs",
    "core/store/src/flat/manager.rs",
    "core/store/src/flat/storage.rs",
    "core/store/src/flat/types.rs",
    "core/store/src/merkle_proof.rs",
    "core/store/src/trie/from_flat.rs",
    "core/store/src/trie/iterator.rs",
    "core/store/src/trie/mem/loading.rs",
    "core/store/src/trie/mem/memtries.rs",
    "core/store/src/trie/mem/memtrie_update.rs",
    "core/store/src/trie/ops/insert_delete.rs",
    "core/store/src/trie/ops/interface.rs",
    "core/store/src/trie/ops/iter.rs",
    "core/store/src/trie/ops/resharding.rs",
    "core/store/src/trie/ops/squash.rs",
    "core/store/src/trie/raw_node.rs",
    "core/store/src/trie/receipts_column_helper.rs",
    "core/store/src/trie/shard_tries.rs",
    "core/store/src/trie/split.rs",
    "core/store/src/trie/state_parts.rs",
    "core/store/src/trie/state_snapshot.rs",
    "core/store/src/trie/trie_recording.rs",
    "core/store/src/trie/trie_storage.rs",
    "core/store/src/trie/trie_storage_update.rs",
    "core/store/src/trie/update.rs",
    "nearcore/src/config_validate.rs",
    "nearcore/src/state_sync.rs",
    "neard/src/cli.rs",
    "neard/src/main.rs",
    "runtime/near-vm-runner/src/cache.rs",
    "runtime/near-vm-runner/src/features.rs",
    "runtime/near-vm-runner/src/imports.rs",
    "runtime/near-vm-runner/src/logic/alt_bn128.rs",
    "runtime/near-vm-runner/src/logic/bls12381.rs",
    "runtime/near-vm-runner/src/logic/context.rs",
    "runtime/near-vm-runner/src/logic/gas_counter.rs",
    "runtime/near-vm-runner/src/logic/logic.rs",
    "runtime/near-vm-runner/src/logic/recorded_storage_counter.rs",
    "runtime/near-vm-runner/src/logic/vmstate.rs",
    "runtime/near-vm-runner/src/prepare.rs",
    "runtime/near-vm-runner/src/prepare/instrument_v3.rs",
    "runtime/near-vm-runner/src/prepare/prepare_v2.rs",
    "runtime/near-vm-runner/src/prepare/prepare_v3.rs",
    "runtime/near-vm-runner/src/runner.rs",
    "runtime/near-vm-runner/src/wasmtime_runner/logic.rs",
    "runtime/near-vm-runner/src/wasmtime_runner/mod.rs",
    "runtime/runtime/src/access_keys.rs",
    "runtime/runtime/src/action_validation.rs",
    "runtime/runtime/src/actions.rs",
    "runtime/runtime/src/adapter.rs",
    "runtime/runtime/src/bandwidth_scheduler/distribute_remaining.rs",
    "runtime/runtime/src/bandwidth_scheduler/scheduler.rs",
    "runtime/runtime/src/cache_warming.rs",
    "runtime/runtime/src/congestion_control.rs",
    "runtime/runtime/src/contract_code.rs",
    "runtime/runtime/src/conversions.rs",
    "runtime/runtime/src/deterministic_account_id.rs",
    "runtime/runtime/src/ext.rs",
    "runtime/runtime/src/function_call.rs",
    "runtime/runtime/src/global_contracts.rs",
    "runtime/runtime/src/pipelining.rs",
    "runtime/runtime/src/prefetch.rs",
    "runtime/runtime/src/receipt_manager.rs",
    "runtime/runtime/src/types.rs",
    "runtime/runtime/src/verifier.rs",
]

target_scopes = [
    "Critical. Unprivileged-user-triggered Consensus divergence in block, chunk, header, approval, finality, epoch, or shard-layout validation lets honest nearcore nodes accept different canonical chains or finalized blocks.",
    "Critical. Unprivileged-user-triggered Runtime apply, transaction, receipt, action, gas, refund, storage, promise, yield/resume, or WASM host logic produces a different state root, outcome, balance, nonce, or receipt set for the same valid input.",
    "Critical. Unprivileged-user-triggered State trie, flat storage, state parts, Merkle proofs, state witness, stateless validation, or chunk endorsement logic accepts forged state/chunks or rejects valid state in a consensus path.",
    "Critical. Unprivileged-user-triggered Dynamic resharding, shard assignment, epoch transition, protocol feature gating, or shard tracking computes an invalid shard layout, split boundary, validator assignment, or catchup requirement.",
    "Critical. Unprivileged-user-triggered Signature, account key, validator signer, approval, endorsement, VRF, light client, epoch sync proof, or hash-domain bug verifies an invalid proof/signature or rejects a valid one in consensus code.",
    "High. Unprivileged-user-triggered Mempool, RPC transaction submission, access-key permission, nonce, gas-key, or transaction validation path admits invalid transactions or rejects valid transactions before block inclusion.",
    "High. Unprivileged-user-triggered Network protocol, peer routing, chunk/state witness distribution, partial witness encoding, Reed-Solomon reconstruction, or state sync messages can be forged, replayed, misrouted, or misattributed with chain safety impact.",
    "High. Unprivileged-user-triggered Sync, epoch sync, header sync, block sync, state sync, orphan handling, or missing-chunk logic can make a node follow invalid data, skip required validation, or fail to recover canonical state.",
    "High. Unprivileged-user-triggered Congestion control, bandwidth scheduler, delayed/buffered/postponed receipts, outgoing limits, or cross-shard routing loses, duplicates, reorders, or over-admits work with state-transition impact.",
]


def question_generator(target_file: str) -> str:
    """
    Generate exploit-focused audit and fuzzing questions for one nearcore target.

    target_file format:
    "'File Name: chain/chain/src/validate.rs -> Scope: Critical. Unprivileged-user-triggered Consensus divergence in block, chunk, header, approval, finality, epoch, or shard-layout validation lets honest nearcore nodes accept different canonical chains or finalized blocks.'"
    """

    prompt = f"""
    ```

    Generate exploit-focused security audit and fuzzing questions for this exact nearcore target:

    {target_file}

    Project focus:
    This repository is `nearcore`, the Rust reference implementation of NEAR Protocol. Security impact is consensus or consensus-adjacent correctness: block/chunk/header validity, Doomslug approvals/finality, epoch and validator assignment, shard layouts and dynamic resharding, runtime state transitions, gas/accounting, receipts, trie/state roots, state sync, stateless validation, signatures, peer messages, RPC transaction admission, and protocol feature gates.

    Use concrete mechanisms when relevant: `Chain::start_process_block_async`, `preprocess_block`, `validate_block`, `validate_chunk_with_chunk_extra_and_roots`, `Doomslug`, approvals, `EpochManager`, `ShardLayoutV3`, `get_upcoming_shard_split`, `NightshadeRuntime::apply`, `TrieUpdate`, `ShardTries`, `find_trie_split`, `StateRoot`, receipts, access keys, gas keys, congestion control, bandwidth scheduler, state witnesses, chunk endorsements, partial witnesses, sync handlers, and Borsh/JSON/RPC/network message boundaries.

    Analyst mindset:
    * Think like an exploit engineer finding chain splits or invalid state transitions, not a linter.
    * Infer the file's role first, then generate only questions that fit that role and the given scope.
    * Reason in transitions: peer/RPC input -> validation -> epoch/shard/runtime/sync state -> persisted roots/outcomes/messages.
    * Prefer multi-step questions about adversarial blocks, chunks, receipts, transactions, witnesses, state parts, epoch proofs, shard splits, gas limits, protocol versions, and cache/store reuse.
    * If the file is a helper, target production callers that rely on its exact output.

    Core invariants:
    * The same valid block/chunk/receipt/state input must produce the same state root, outcome root, gas/balance changes, receipts, and head/finality on all honest nodes.
    * Invalid signatures, approvals, endorsements, epoch proofs, state parts, witnesses, chunks, headers, shard splits, and transactions must be rejected before state changes.
    * Runtime apply must preserve gas, refunds, nonces, permissions, receipt DAG dependencies, congestion limits, and account/storage invariants.
    * Trie, flat storage, memtrie, state sync, stateless validation, and resharding must agree on keys, roots, memory usage, shard ownership, and state parts.
    * Protocol features, epoch boundaries, shard layouts, validator assignment, and sync/catchup must be deterministic from chain state.

    Rules:
    * Treat `File Name:` as the exact file/module.
    * Treat `Scope:` as the ONLY impact to target.
    * Assume full repo context is accessible.
    * Do not ask for code or say anything is missing.
    * Attacker must be an unprivileged external user: ordinary account holder, contract deployer/caller, public RPC client, or unauthenticated/low-trust peer using public protocol inputs.
    * Unprivileged attacker may control RPC transactions, signed tx fields for their own keys/accounts, contract code/input, public network messages, peer-supplied blocks/chunks/state data, or timing/order of data they are allowed to send.
    * Do not grant the attacker validator, block producer, chunk producer, chunk validator, signer, relayer operator, node admin, or trusted infrastructure privileges unless the bug lets an unprivileged user bypass that requirement.
    * A malicious peer sending malformed, stale, conflicting, oversized, or unsolicited data is not a finding by itself; only ask about cases where nearcore accepts it as valid, skips a required check, corrupts canonical state, or makes a wrong scoped decision.
    * Do not rely on malicious maintainers, admin/operator mistakes, validator/chunk-producer equivocation, compromised validator supermajorities, unsupported local config, unsafe manual DB edits, bad genesis files outside the threat model, social engineering, dependency-only bugs, or downstream misuse outside nearcore APIs.
    * Exclude ordinary crashes, pure DoS/performance, unbounded CPU/memory/disk/cache/queue growth, Rust allocation/lifetime/clone issues, OOM, leaks, logs, docs, tests, mocks, benches, generated data, tooling, and best practices without scoped impact.
    * Do not turn resource exhaustion into High/Critical unless it deterministically causes an accepted invalid block/chunk/state, wrong state transition, wrong finality/head, or invalid transaction admission that survives normal validation.
    * Generate 18 to 26 high-signal questions.
    * At least 70% must trace across 2+ modules or state transitions.
    * Every question must be testable by `cargo test --features test_features`, a Rust property/fuzz test, a test-loop test, or a focused local reproducer.
    * Avoid generic checklist questions and repeated root causes.
    * Each question must target a plausible issue class for the exact file and scope.
    * Anchor to concrete symbols when possible: functions, structs, protocol features, columns, roots, caches, fields, message types, or DB keys.
    * Name the exact value that may diverge: state root, outcome root, block/chunk validity, finality, epoch id, shard id/layout, validator set, gas, balance, nonce, receipt, state part, witness, signature result, DB entry, or RPC admission result.

    High-value attack surfaces:
    * `chain/chain`: block processing, Doomslug, validation, state sync, stateless validation, SPICE, resharding.
    * `runtime/runtime` and `runtime/near-vm-runner`: runtime apply, receipts, actions, gas, permissions, storage, WASM host functions.
    * `core/primitives` and `core/primitives-core`: consensus data types, serialization, hashes, shard layouts, transactions, receipts, state parts.
    * `core/store`: trie, flat storage, memtrie, state parts, Merkle proofs, resharding state.
    * `chain/client`, `chain/epoch-manager`, `chain/chunks`, `chain/network`, `chain/pool`, `chain/jsonrpc`: sync, epoch proofs, chunk production, peer/RPC entrypoints, mempool.

    Impact mapping:
    * High/Critical only: consensus divergence, invalid block/chunk/state acceptance, wrong runtime result, forged proof/signature acceptance, incorrect shard/epoch/validator assignment, invalid transaction admission, or cross-shard/state sync corruption.

    Each question must include:

    1. target function/module;
    2. attacker-controlled input;
    3. preconditions;
    4. call sequence;
    5. invariant tested;
    6. scoped impact;
    7. proof idea.

    Output only valid Python. No markdown. No explanations.

    questions = [
    "[File: {target_file}] [Function: symbol_or_module] Can attacker-controlled INPUT under PRECONDITIONS trigger CALL_SEQUENCE, violating INVARIANT, causing scoped impact: SCOPE_IMPACT? Proof idea: run a Rust unit/property/fuzz/test-loop reproducer over PARAMETERS and assert EXPECTED_PROPERTY.",
    ]
    """
    return prompt


def audit_format(question: str) -> str:
    """
    Generate a focused nearcore exploit-question validation prompt.
    """
    return f"""# QUESTION SCAN PROMPT

## Exploit Question
{question}

## Scope Rules
- Audit only production nearcore repository code listed in `scope_files`.
- Do not ask for repo contents or claim files are missing.
- Ignore tests, docs, mocks, test utilities, fuzz harnesses, benches, generated data, repo automation, packaging metadata, and local-only tooling.

## Objective
Decide whether the question leads to a real, reachable nearcore vulnerability.
The attacker must be an unprivileged external user entering through supported production behavior: public RPC transactions, transaction fields signed by their own keys, contract input/code they can deploy or call, public peer messages, peer-supplied blocks/chunks/state data, or repeated public API/cache use.
Do not assume validator, block producer, chunk producer, chunk validator, node admin, relayer operator, or trusted-service privileges unless the claim proves an unprivileged user can bypass that authority boundary.
The impact must match the provided target scope.
Prefer #NoVulnerability unless the path is concrete, locally testable, and proves High/Critical consensus, runtime, mempool, sync, stateless validation, or RPC admission impact.
Malicious peer behavior is only a trigger, not an impact: reject reports where the whole issue is that a peer can send malformed, stale, duplicated, conflicting, oversized, or unsolicited data and nearcore rejects, ignores, rate-limits, disconnects, retries, or only wastes resources.
Treat the question as a hypothesis. Look for the exact state root, outcome root, finality/head, block/chunk validity, epoch id, shard layout, validator assignment, gas, balance, nonce, receipt, state part, witness, signature result, DB entry, or RPC result that would make it real.

## Method
1. Trace the attacker-controlled entrypoint.
2. Map it to exact production repository files and functions.
3. Check relevant guards: signatures, stakes, approvals, chunk endorsements, epoch boundaries, protocol features, shard layouts, gas/accounting, access-key permissions, nonce checks, receipt dependencies, trie roots, state-part proofs, witness validation, sync proof checks, cache keys, and DB column invariants.
4. Identify the exact state or output that must change for the exploit to work.
5. Decide whether the questioned invariant can actually break under intended API use.
6. Prove root cause with file, function, and line references.
7. Confirm realistic likelihood and exact scoped impact.
8. Reject if current validation already prevents the exploit.

## Reject Immediately
- Requires malicious maintainers, compromised nodes, downstream misuse outside this API contract, unsupported local configuration, social engineering, or dependency-only behavior.
- Requires admin/operator mistakes, unsafe manual DB/config/genesis edits, wrong key management, debug/adversarial modes, or non-production flags.
- Requires validator, block producer, chunk producer, chunk validator, relayer operator, node admin, or trusted infrastructure privileges not obtainable by an unprivileged user.
- Is only malicious-peer noise: malformed/stale/duplicated/conflicting/oversized/unsolicited peer data without accepted invalid protocol state or skipped required validation.
- Only affects tests, docs, mocks, test utilities, fuzz harnesses, benches, generated data, automation, packaging, or local tooling.
- Impact is ordinary crash, denial of service, performance-only degradation, unbounded CPU/memory/disk/cache/queue growth, memory leak/OOM/allocation pressure, logging/display issue, harmless rejection, style, or best practice.
- Depends only on Rust memory-management concerns such as clones, drops, lifetimes, reference counts, buffer growth, or cache retention without a concrete scoped state/consensus result.
- No concrete scoped impact or no realistic attacker-controlled API path.
- No exact state root, outcome root, block/chunk validity, finality/head, epoch id, shard id/layout, validator set, gas, balance, nonce, receipt, state part, witness, signature result, DB entry, or RPC admission delta can be named.
- The question depends on impossible NEAR protocol behavior or privileges not granted by the scoped code path.

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
    Generate a short cross-project analog scan prompt for the nearcore repository.
    """
    prompt = f"""# ANALOG SCAN PROMPT

## External Report
{report}

## Access Rules (Strict)
- Treat production nearcore repository files in the provided scope as accessible context.
- Do not claim missing or inaccessible files.
- Do not ask for repository contents.
- Do not scan tests, docs, mocks, test utilities, fuzz harnesses, benches, generated data, repo automation, packaging metadata, or local-only tooling as audited targets.

## Objective
Use the external report's vulnerability class as a hint to find valid issues based on this repository's security impact.
Focus on issues triggered by an unprivileged external user through public RPC transactions, their own signed transaction fields, deployable/callable contract code/input, public peer messages, peer-supplied protocol data, or repeated public API/cache use.
Do not use analogs that require validator, block producer, chunk producer, chunk validator, node admin, relayer operator, or trusted-service privileges unless the analog is precisely an unprivileged bypass of that boundary.
Only report an analog if this repository has its own reachable root cause and the impact matches the provided target scope.
Be strict about analog quality: similarity of bug class is not enough. This repository must have its own concrete trigger, broken invariant, and scoped impact.
Do not report analogs that amount to admin/operator mistakes, malicious-peer noise, ordinary resource exhaustion, unbounded memory/storage/cache/queue growth, or Rust memory-management cleanup concerns without a concrete High/Critical protocol result.

## Method
1. Classify vuln type: consensus divergence, invalid block/chunk acceptance, runtime state mismatch, gas/refund bug, signature/proof bypass, shard/epoch transition bug, trie/state root mismatch, sync/stateless validation bypass, mempool/RPC admission bug, or network message confusion.
2. Map to exact production files and modules.
3. Identify the exact state root, outcome root, validity decision, finality/head, epoch/shard value, validator set, gas, balance, nonce, receipt, state part, witness, signature result, DB entry, or RPC result that the analog would corrupt.
4. Prove root cause with exact file, function, module, and line references.
5. Confirm concrete scoped impact and realistic likelihood.
6. Explain the attacker-controlled entry path and why repository code is a necessary vulnerable step.
7. Reject if the impact does not match the provided target scope.

## Disqualify Immediately
- No reachable attacker-controlled entry path.
- Requires malicious maintainers, compromised nodes, unsupported local configuration, social engineering, or dependency-only behavior.
- Requires admin/operator mistakes, unsafe manual DB/config/genesis edits, wrong key management, debug/adversarial modes, or non-production flags.
- Requires validator, block producer, chunk producer, chunk validator, relayer operator, node admin, or trusted infrastructure privileges not obtainable by an unprivileged user.
- Only shows that a malicious peer can send malformed, stale, duplicate, conflicting, oversized, or unsolicited data that nearcore rejects, ignores, rate-limits, disconnects, retries, or treats as non-canonical.
- Test, docs, mocks, fuzz harness, bench, generated data, automation, packaging, or local-tooling issue.
- Theoretical-only issue with no consensus, runtime, sync, mempool, stateless validation, or RPC admission impact.
- Impact is ordinary crash, denial of service, performance-only degradation, unbounded CPU/memory/disk/cache/queue growth, memory leak/OOM/allocation pressure, logging/display noise, harmless rejection, style, or best practice.
- Root cause is only Rust memory-management hygiene such as clones, drops, lifetimes, reference counts, retained buffers, cache sizing, or queue growth without corrupting a scoped protocol value.
- Impact or likelihood missing.
- No exact corrupted state root, outcome root, validity decision, finality/head, epoch/shard value, validator set, gas, balance, nonce, receipt, state part, witness, signature result, DB entry, or RPC result can be identified.

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
    Generate a strict nearcore validation prompt for security claims.
    """
    prompt = f"""# VALIDATION PROMPT

## Security Claim
{report}

## Rules
- Validate only the submitted claim.
- Validate against this repository's production nearcore scope and the allowed impact classes below.
- Do not create a new vulnerability if the submitted claim is weak or invalid.
- Do not upgrade severity unless the provided evidence proves the higher impact.
- Reject malicious-maintainer, compromised-node, downstream-misuse, unsupported-config, docs/style, ordinary-crash, denial-of-service, performance-only, dependency-only, and purely theoretical issues.
- Reject admin/operator mistakes, unsafe manual DB/config/genesis edits, wrong key management, debug/adversarial modes, non-production flags, and environment-specific deployment mistakes.
- Reject claims requiring validator, block producer, chunk producer, chunk validator, relayer operator, node admin, or trusted infrastructure privileges unless the report proves an unprivileged user can bypass that boundary.
- Reject malicious-peer-only claims where peers send malformed, stale, duplicated, conflicting, oversized, or unsolicited data but nearcore rejects, ignores, rate-limits, disconnects, retries, or only wastes resources.
- Reject unbounded CPU/memory/disk/cache/queue growth, leaks, OOM, allocation pressure, and Rust memory-management cleanup issues unless the evidence proves a deterministic accepted invalid block/chunk/state, wrong state transition, wrong finality/head, or invalid transaction admission.
- Reject if the exploit requires unrealistic assumptions, victim mistakes, missing external context, or unsupported NEAR protocol behavior.
- A valid report must be triggerable by an unprivileged external user through public RPC transactions, transaction fields signed by their own keys, contract input/code they can deploy or call, public peer messages, peer-supplied protocol data, or repeated public API/cache use.
- The final impact must match one of the High/Critical `target_scopes`, not just a generic code bug.
- Prefer #NoVulnerability over speculative reports.
- Be skeptical of reports that describe a bug class without naming the exact state root, outcome root, validity decision, finality/head, epoch/shard value, validator set, gas, balance, nonce, receipt, state part, witness, signature result, DB entry, or RPC result produced by the exploit.

## Allowed Impact Scope
Only these impacts are valid:
- Critical. Unprivileged-user-triggered Consensus divergence in block, chunk, header, approval, finality, epoch, or shard-layout validation lets honest nearcore nodes accept different canonical chains or finalized blocks.
- Critical. Unprivileged-user-triggered Runtime apply, transaction, receipt, action, gas, refund, storage, promise, yield/resume, or WASM host logic produces a different state root, outcome, balance, nonce, or receipt set for the same valid input.
- Critical. Unprivileged-user-triggered State trie, flat storage, state parts, Merkle proofs, state witness, stateless validation, or chunk endorsement logic accepts forged state/chunks or rejects valid state in a consensus path.
- Critical. Unprivileged-user-triggered Dynamic resharding, shard assignment, epoch transition, protocol feature gating, or shard tracking computes an invalid shard layout, split boundary, validator assignment, or catchup requirement.
- Critical. Unprivileged-user-triggered Signature, account key, validator signer, approval, endorsement, VRF, light client, epoch sync proof, or hash-domain bug verifies an invalid proof/signature or rejects a valid one in consensus code.
- High. Unprivileged-user-triggered Mempool, RPC transaction submission, access-key permission, nonce, gas-key, or transaction validation path admits invalid transactions or rejects valid transactions before block inclusion.
- High. Unprivileged-user-triggered Network protocol, peer routing, chunk/state witness distribution, partial witness encoding, Reed-Solomon reconstruction, or state sync messages can be forged, replayed, misrouted, or misattributed with chain safety impact.
- High. Unprivileged-user-triggered Sync, epoch sync, header sync, block sync, state sync, orphan handling, or missing-chunk logic can make a node follow invalid data, skip required validation, or fail to recover canonical state.
- High. Unprivileged-user-triggered Congestion control, bandwidth scheduler, delayed/buffered/postponed receipts, outgoing limits, or cross-shard routing loses, duplicates, reorders, or over-admits work with state-transition impact.

If the submitted claim does not concretely prove one of the allowed impacts above, it is invalid.

## Required Validation Checks
All must pass:
1. Exact in-scope file, function, and line or code references.
2. Clear root cause and broken consensus, runtime, serialization, hashing, signature, epoch, shard, trie, sync, witness, gas, mempool, or routing assumption.
3. Reachable exploit path: preconditions -> attacker input -> trigger -> bad result.
4. Existing checks or guards reviewed and shown insufficient.
5. Exact corrupted value identified: what state root, outcome root, validity decision, finality/head, epoch/shard value, validator set, gas, balance, nonce, receipt, state part, witness, signature result, DB entry, or RPC result changed incorrectly.
6. Concrete impact that exactly matches one allowed repository impact above, with realistic likelihood.
7. Reproducible proof path: Rust unit/property test, fuzz target, test-loop test, protocol state test, or justified local reproducer.
8. No obvious rejection reason from assumptions, privileged-role requirements, admin/operator error, malicious-peer-only behavior, resource-only behavior, dependency-only behavior, or scope exclusions.

## Silent Triage Questions
Before output, internally answer:
- Can attacker-controlled peer/RPC/contract/state-sync input trigger this?
- Can an unprivileged user trigger this without validator, block producer, chunk producer, node admin, relayer operator, or trusted-service privileges?
- Is the issue more than a malicious peer sending bad data that is rejected or only wastes resources?
- Is the issue more than admin/operator misconfiguration or local environment setup?
- Is the issue more than unbounded resource growth, OOM, cache retention, queue growth, or Rust memory-management hygiene?
- Does the code actually behave as claimed?
- Is the impact caused by this repository, not by an external dependency alone?
- Is the consensus/runtime/sync/mempool impact concrete, not hypothetical?
- What exact state root, outcome root, validity decision, finality/head, epoch/shard value, validator set, gas, balance, nonce, receipt, state part, witness, signature result, DB entry, or RPC result is wrong after the exploit?
- What consensus, runtime, gas, trie, signature, epoch, shard, sync, witness, mempool, routing, or serialization rule is broken?
- Would a security triager accept the proof?
- What exact test would prove it?

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
[Minimal reproducible steps or fuzz, differential, property, or state test plan]

If invalid, output exactly:
#NoVulnerability found for this question.

Output only one of the two outcomes above. No extra text.
"""
    return prompt
