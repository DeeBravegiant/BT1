import json
import os

from decouple import config

# todo: if scope_files is: 500 > 50, 300 > 30 , 100 > 10
MAX_REPO = 20
# todo: the path from https:///github.com/dfinity/ICRC-1
SOURCE_REPO = "near/mpc"
# todo: the name of the repository
REPO_NAME = "mpc"
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
    "crates/attestation/src/app_compose.rs",
    "crates/attestation/src/attestation.rs",
    "crates/attestation/src/collateral.rs",
    "crates/attestation/src/dcap_conversions.rs",
    "crates/attestation/src/lib.rs",
    "crates/attestation/src/measurements.rs",
    "crates/attestation/src/quote.rs",
    "crates/attestation/src/report_data.rs",
    "crates/attestation/src/tcb_info.rs",
    "crates/chain-gateway/src/chain_gateway.rs",
    "crates/chain-gateway/src/errors.rs",
    "crates/chain-gateway/src/event_subscriber.rs",
    "crates/chain-gateway/src/event_subscriber/block_events.rs",
    "crates/chain-gateway/src/event_subscriber/consts.rs",
    "crates/chain-gateway/src/event_subscriber/metrics.rs",
    "crates/chain-gateway/src/event_subscriber/recent_blocks_tracker.rs",
    "crates/chain-gateway/src/event_subscriber/stats.rs",
    "crates/chain-gateway/src/event_subscriber/streamer.rs",
    "crates/chain-gateway/src/event_subscriber/streamer/block_processor.rs",
    "crates/chain-gateway/src/event_subscriber/streamer/config.rs",
    "crates/chain-gateway/src/event_subscriber/subscriber.rs",
    "crates/chain-gateway/src/lib.rs",
    "crates/chain-gateway/src/near_internals_wrapper.rs",
    "crates/chain-gateway/src/near_internals_wrapper/client.rs",
    "crates/chain-gateway/src/near_internals_wrapper/rpc.rs",
    "crates/chain-gateway/src/near_internals_wrapper/view_client.rs",
    "crates/chain-gateway/src/primitives.rs",
    "crates/chain-gateway/src/state_viewer.rs",
    "crates/chain-gateway/src/state_viewer/monitoring.rs",
    "crates/chain-gateway/src/state_viewer/subscription.rs",
    "crates/chain-gateway/src/state_viewer/traits.rs",
    "crates/chain-gateway/src/transaction_sender.rs",
    "crates/chain-gateway/src/transaction_sender/signer.rs",
    "crates/chain-gateway/src/transaction_sender/traits.rs",
    "crates/chain-gateway/src/types.rs",
    "crates/contract-history/src/lib.rs",
    "crates/contract/src/config.rs",
    "crates/contract/src/crypto_shared.rs",
    "crates/contract/src/crypto_shared/kdf.rs",
    "crates/contract/src/crypto_shared/types.rs",
    "crates/contract/src/crypto_shared/types/serializable.rs",
    "crates/contract/src/dto_mapping.rs",
    "crates/contract/src/errors.rs",
    "crates/contract/src/foreign_chain_rpc.rs",
    "crates/contract/src/foreign_chains_metadata.rs",
    "crates/contract/src/lib.rs",
    "crates/contract/src/node_migrations.rs",
    "crates/contract/src/pending_requests.rs",
    "crates/contract/src/primitives.rs",
    "crates/contract/src/primitives/ckd.rs",
    "crates/contract/src/primitives/domain.rs",
    "crates/contract/src/primitives/key_state.rs",
    "crates/contract/src/primitives/participants.rs",
    "crates/contract/src/primitives/signature.rs",
    "crates/contract/src/primitives/threshold_votes.rs",
    "crates/contract/src/primitives/thresholds.rs",
    "crates/contract/src/primitives/time.rs",
    "crates/contract/src/primitives/votes.rs",
    "crates/contract/src/state.rs",
    "crates/contract/src/state/initializing.rs",
    "crates/contract/src/state/key_event.rs",
    "crates/contract/src/state/resharing.rs",
    "crates/contract/src/state/running.rs",
    "crates/contract/src/storage_keys.rs",
    "crates/contract/src/tee.rs",
    "crates/contract/src/tee/measurements.rs",
    "crates/contract/src/tee/proposal.rs",
    "crates/contract/src/tee/tee_state.rs",
    "crates/contract/src/tee/verifier_votes.rs",
    "crates/contract/src/update.rs",
    "crates/contract/src/utils.rs",
    "crates/contract/src/v3_12_0_state.rs",
    "crates/foreign-chain-inspector/src/abstract_chain.rs",
    "crates/foreign-chain-inspector/src/abstract_chain/inspector.rs",
    "crates/foreign-chain-inspector/src/aptos.rs",
    "crates/foreign-chain-inspector/src/aptos/inspector.rs",
    "crates/foreign-chain-inspector/src/arbitrum.rs",
    "crates/foreign-chain-inspector/src/arbitrum/inspector.rs",
    "crates/foreign-chain-inspector/src/base.rs",
    "crates/foreign-chain-inspector/src/base/inspector.rs",
    "crates/foreign-chain-inspector/src/bitcoin.rs",
    "crates/foreign-chain-inspector/src/bitcoin/inspector.rs",
    "crates/foreign-chain-inspector/src/bnb.rs",
    "crates/foreign-chain-inspector/src/bnb/inspector.rs",
    "crates/foreign-chain-inspector/src/contract_interface_conversions.rs",
    "crates/foreign-chain-inspector/src/evm.rs",
    "crates/foreign-chain-inspector/src/evm/inspector.rs",
    "crates/foreign-chain-inspector/src/hyperevm.rs",
    "crates/foreign-chain-inspector/src/hyperevm/inspector.rs",
    "crates/foreign-chain-inspector/src/lib.rs",
    "crates/foreign-chain-inspector/src/polygon.rs",
    "crates/foreign-chain-inspector/src/polygon/inspector.rs",
    "crates/foreign-chain-inspector/src/starknet.rs",
    "crates/foreign-chain-inspector/src/starknet/inspector.rs",
    "crates/foreign-chain-rpc-auth/src/lib.rs",
    "crates/foreign-chain-rpc-interfaces/src/aptos.rs",
    "crates/foreign-chain-rpc-interfaces/src/bitcoin.rs",
    "crates/foreign-chain-rpc-interfaces/src/evm.rs",
    "crates/foreign-chain-rpc-interfaces/src/lib.rs",
    "crates/foreign-chain-rpc-interfaces/src/starknet.rs",
    "crates/include-measurements/src/lib.rs",
    "crates/launcher-interface/src/lib.rs",
    "crates/launcher-interface/src/types.rs",
    "crates/mpc-attestation/src/attestation.rs",
    "crates/mpc-attestation/src/lib.rs",
    "crates/mpc-attestation/src/report_data.rs",
    "crates/mpc-call-args/src/lib.rs",
    "crates/near-mpc-bounded-collections/src/bounded_vec.rs",
    "crates/near-mpc-bounded-collections/src/btreemap.rs",
    "crates/near-mpc-bounded-collections/src/btreeset.rs",
    "crates/near-mpc-bounded-collections/src/lib.rs",
    "crates/near-mpc-contract-interface/src/call_args.rs",
    "crates/near-mpc-contract-interface/src/lib.rs",
    "crates/near-mpc-contract-interface/src/method_names.rs",
    "crates/near-mpc-contract-interface/src/types/attestation.rs",
    "crates/near-mpc-contract-interface/src/types/ckd.rs",
    "crates/near-mpc-contract-interface/src/types/config.rs",
    "crates/near-mpc-contract-interface/src/types/foreign_chain.rs",
    "crates/near-mpc-contract-interface/src/types/metrics.rs",
    "crates/near-mpc-contract-interface/src/types/node_migrations.rs",
    "crates/near-mpc-contract-interface/src/types/participants.rs",
    "crates/near-mpc-contract-interface/src/types/primitives.rs",
    "crates/near-mpc-contract-interface/src/types/sign.rs",
    "crates/near-mpc-contract-interface/src/types/state.rs",
    "crates/near-mpc-contract-interface/src/types/tee.rs",
    "crates/near-mpc-contract-interface/src/types/updates.rs",
    "crates/near-mpc-crypto-types/src/ckd.rs",
    "crates/near-mpc-crypto-types/src/conversions.rs",
    "crates/near-mpc-crypto-types/src/conversions/blstrs.rs",
    "crates/near-mpc-crypto-types/src/conversions/ed25519_dalek.rs",
    "crates/near-mpc-crypto-types/src/conversions/k256.rs",
    "crates/near-mpc-crypto-types/src/conversions/near.rs",
    "crates/near-mpc-crypto-types/src/crypto.rs",
    "crates/near-mpc-crypto-types/src/kdf.rs",
    "crates/near-mpc-crypto-types/src/key_state.rs",
    "crates/near-mpc-crypto-types/src/lib.rs",
    "crates/near-mpc-crypto-types/src/primitives.rs",
    "crates/near-mpc-crypto-types/src/sign.rs",
    "crates/near-mpc-sdk/src/foreign_chain.rs",
    "crates/near-mpc-sdk/src/foreign_chain/abstract_chain.rs",
    "crates/near-mpc-sdk/src/foreign_chain/arbitrum.rs",
    "crates/near-mpc-sdk/src/foreign_chain/base.rs",
    "crates/near-mpc-sdk/src/foreign_chain/bitcoin.rs",
    "crates/near-mpc-sdk/src/foreign_chain/bnb.rs",
    "crates/near-mpc-sdk/src/foreign_chain/evm.rs",
    "crates/near-mpc-sdk/src/foreign_chain/hyper_evm.rs",
    "crates/near-mpc-sdk/src/foreign_chain/polygon.rs",
    "crates/near-mpc-sdk/src/foreign_chain/starknet.rs",
    "crates/near-mpc-sdk/src/lib.rs",
    "crates/near-mpc-sdk/src/sign.rs",
    "crates/near-mpc-signature-verifier/src/lib.rs",
    "crates/node-config/src/foreign_chains.rs",
    "crates/node-config/src/foreign_chains/auth.rs",
    "crates/node-config/src/lib.rs",
    "crates/node-config/src/start.rs",
    "crates/node-types/src/http_server.rs",
    "crates/node-types/src/lib.rs",
    "crates/node/src/assets.rs",
    "crates/node/src/assets/cleanup.rs",
    "crates/node/src/background.rs",
    "crates/node/src/cli.rs",
    "crates/node/src/config.rs",
    "crates/node/src/config/start.rs",
    "crates/node/src/coordinator.rs",
    "crates/node/src/db.rs",
    "crates/node/src/foreign_chain_whitelist_verifier.rs",
    "crates/node/src/home_paths.rs",
    "crates/node/src/indexer.rs",
    "crates/node/src/indexer/configs.rs",
    "crates/node/src/indexer/handler.rs",
    "crates/node/src/indexer/migrations.rs",
    "crates/node/src/indexer/near_data_wipe.rs",
    "crates/node/src/indexer/participants.rs",
    "crates/node/src/indexer/real.rs",
    "crates/node/src/indexer/stats.rs",
    "crates/node/src/indexer/tee.rs",
    "crates/node/src/indexer/tx_sender.rs",
    "crates/node/src/indexer/tx_signer.rs",
    "crates/node/src/indexer/types.rs",
    "crates/node/src/key_events.rs",
    "crates/node/src/keyshare.rs",
    "crates/node/src/keyshare/compat.rs",
    "crates/node/src/keyshare/gcp.rs",
    "crates/node/src/keyshare/local.rs",
    "crates/node/src/keyshare/permanent.rs",
    "crates/node/src/keyshare/temporary.rs",
    "crates/node/src/lib.rs",
    "crates/node/src/main.rs",
    "crates/node/src/metrics.rs",
    "crates/node/src/metrics/networking_metrics.rs",
    "crates/node/src/metrics/tokio_runtime_metrics.rs",
    "crates/node/src/metrics/tokio_task_metrics.rs",
    "crates/node/src/migration_service.rs",
    "crates/node/src/migration_service/onboarding.rs",
    "crates/node/src/migration_service/types.rs",
    "crates/node/src/migration_service/web.rs",
    "crates/node/src/migration_service/web/authentication.rs",
    "crates/node/src/migration_service/web/client.rs",
    "crates/node/src/migration_service/web/encryption.rs",
    "crates/node/src/migration_service/web/serialization.rs",
    "crates/node/src/migration_service/web/server.rs",
    "crates/node/src/migration_service/web/types.rs",
    "crates/node/src/mpc_client.rs",
    "crates/node/src/network.rs",
    "crates/node/src/network/computation.rs",
    "crates/node/src/network/conn.rs",
    "crates/node/src/network/constants.rs",
    "crates/node/src/network/handshake.rs",
    "crates/node/src/network/indexer_heights.rs",
    "crates/node/src/p2p.rs",
    "crates/node/src/primitives.rs",
    "crates/node/src/profiler.rs",
    "crates/node/src/profiler/jemalloc.rs",
    "crates/node/src/profiler/pprof.rs",
    "crates/node/src/profiler/web_server.rs",
    "crates/node/src/protocol.rs",
    "crates/node/src/protocol_version.rs",
    "crates/node/src/providers.rs",
    "crates/node/src/providers/ckd.rs",
    "crates/node/src/providers/ckd/key_generation.rs",
    "crates/node/src/providers/ckd/key_resharing.rs",
    "crates/node/src/providers/ckd/sign.rs",
    "crates/node/src/providers/ecdsa.rs",
    "crates/node/src/providers/ecdsa/key_generation.rs",
    "crates/node/src/providers/ecdsa/key_resharing.rs",
    "crates/node/src/providers/ecdsa/presign.rs",
    "crates/node/src/providers/ecdsa/sign.rs",
    "crates/node/src/providers/ecdsa/triple.rs",
    "crates/node/src/providers/eddsa.rs",
    "crates/node/src/providers/eddsa/key_generation.rs",
    "crates/node/src/providers/eddsa/key_resharing.rs",
    "crates/node/src/providers/eddsa/sign.rs",
    "crates/node/src/providers/robust_ecdsa.rs",
    "crates/node/src/providers/robust_ecdsa/presign.rs",
    "crates/node/src/providers/robust_ecdsa/sign.rs",
    "crates/node/src/providers/verify_foreign_tx.rs",
    "crates/node/src/providers/verify_foreign_tx/sign.rs",
    "crates/node/src/requests.rs",
    "crates/node/src/requests/debug.rs",
    "crates/node/src/requests/metrics.rs",
    "crates/node/src/requests/queue.rs",
    "crates/node/src/run.rs",
    "crates/node/src/runtime.rs",
    "crates/node/src/storage.rs",
    "crates/node/src/tee.rs",
    "crates/node/src/tee/allowed_image_hashes_watcher.rs",
    "crates/node/src/tee/remote_attestation.rs",
    "crates/node/src/tracing.rs",
    "crates/node/src/tracking.rs",
    "crates/node/src/trait_extensions.rs",
    "crates/node/src/trait_extensions/convert_to_contract_dto.rs",
    "crates/node/src/types.rs",
    "crates/node/src/web.rs",
    "crates/node/src/web/recent_transactions.rs",
    "crates/threshold-signatures/src/confidential_key_derivation.rs",
    "crates/threshold-signatures/src/confidential_key_derivation/app_id.rs",
    "crates/threshold-signatures/src/confidential_key_derivation/ciphersuite.rs",
    "crates/threshold-signatures/src/confidential_key_derivation/protocol.rs",
    "crates/threshold-signatures/src/confidential_key_derivation/protocol_pv.rs",
    "crates/threshold-signatures/src/confidential_key_derivation/scalar_wrapper.rs",
    "crates/threshold-signatures/src/crypto.rs",
    "crates/threshold-signatures/src/crypto/ciphersuite.rs",
    "crates/threshold-signatures/src/crypto/commitment.rs",
    "crates/threshold-signatures/src/crypto/constants.rs",
    "crates/threshold-signatures/src/crypto/hash.rs",
    "crates/threshold-signatures/src/crypto/polynomials.rs",
    "crates/threshold-signatures/src/crypto/polynomials/commitment.rs",
    "crates/threshold-signatures/src/crypto/polynomials/polynomial.rs",
    "crates/threshold-signatures/src/crypto/proofs.rs",
    "crates/threshold-signatures/src/crypto/proofs/dlog.rs",
    "crates/threshold-signatures/src/crypto/proofs/dlogeq.rs",
    "crates/threshold-signatures/src/crypto/proofs/strobe.rs",
    "crates/threshold-signatures/src/crypto/proofs/strobe_transcript.rs",
    "crates/threshold-signatures/src/crypto/random.rs",
    "crates/threshold-signatures/src/dkg.rs",
    "crates/threshold-signatures/src/ecdsa.rs",
    "crates/threshold-signatures/src/ecdsa/ot_based_ecdsa.rs",
    "crates/threshold-signatures/src/ecdsa/ot_based_ecdsa/presign.rs",
    "crates/threshold-signatures/src/ecdsa/ot_based_ecdsa/sign.rs",
    "crates/threshold-signatures/src/ecdsa/ot_based_ecdsa/triples.rs",
    "crates/threshold-signatures/src/ecdsa/ot_based_ecdsa/triples/batch_random_ot.rs",
    "crates/threshold-signatures/src/ecdsa/ot_based_ecdsa/triples/bits.rs",
    "crates/threshold-signatures/src/ecdsa/ot_based_ecdsa/triples/correlated_ot_extension.rs",
    "crates/threshold-signatures/src/ecdsa/ot_based_ecdsa/triples/generation.rs",
    "crates/threshold-signatures/src/ecdsa/ot_based_ecdsa/triples/mta.rs",
    "crates/threshold-signatures/src/ecdsa/ot_based_ecdsa/triples/multiplication.rs",
    "crates/threshold-signatures/src/ecdsa/ot_based_ecdsa/triples/random_ot_extension.rs",
    "crates/threshold-signatures/src/ecdsa/rerandomization.rs",
    "crates/threshold-signatures/src/ecdsa/robust_ecdsa.rs",
    "crates/threshold-signatures/src/ecdsa/robust_ecdsa/presign.rs",
    "crates/threshold-signatures/src/ecdsa/robust_ecdsa/sign.rs",
    "crates/threshold-signatures/src/ecdsa/signature.rs",
    "crates/threshold-signatures/src/errors.rs",
    "crates/threshold-signatures/src/frost.rs",
    "crates/threshold-signatures/src/frost/eddsa.rs",
    "crates/threshold-signatures/src/frost/eddsa/presign.rs",
    "crates/threshold-signatures/src/frost/eddsa/sign.rs",
    "crates/threshold-signatures/src/frost/presign.rs",
    "crates/threshold-signatures/src/frost/redjubjub.rs",
    "crates/threshold-signatures/src/frost/redjubjub/presign.rs",
    "crates/threshold-signatures/src/frost/redjubjub/sign.rs",
    "crates/threshold-signatures/src/frost/sign_utils.rs",
    "crates/threshold-signatures/src/lib.rs",
    "crates/threshold-signatures/src/macros.rs",
    "crates/threshold-signatures/src/participants.rs",
    "crates/threshold-signatures/src/protocol.rs",
    "crates/threshold-signatures/src/protocol/echo_broadcast.rs",
    "crates/threshold-signatures/src/protocol/helpers.rs",
    "crates/threshold-signatures/src/protocol/internal.rs",
    "crates/threshold-signatures/src/thresholds.rs",
]

target_scopes = [
    "Critical. Theft, direct loss, or permanent freezing of funds controlled by the MPC network, chain-signature contract, or verified foreign-chain flow.",
    "Critical. Unauthorized transaction execution, threshold signature issuance, or confidential key derivation output without the required participant authorization.",
    "Critical. Bypass of threshold-signature requirements or unauthorized access to MPC key shares, signing capability, or secret material that materially enables forgery or secret recovery.",
    "High. Cross-chain replay, forged foreign-chain verification, light-client-style verification bypass, or participant/attestation authorization bypass that causes invalid bridge execution or double-spend conditions.",
    "Medium. Balance, request-lifecycle, participant-state, or contract execution-flow manipulation that breaks production safety/accounting invariants without relying on network-level DoS or operator misconfiguration.",
]


def question_generator(target_file: str) -> str:
    """
    Generate exploit-focused audit + fuzzing questions for one NEAR MPC target.

    ```
    target_file format:
    "'File Name: crates/node/src/coordinator.rs -> Scope: Critical. Unauthorized transaction execution, threshold signature issuance, or confidential key derivation output without the required participant authorization.'"
    ```
    """

    prompt = f"""
    ```

    Generate exploit-focused security audit and fuzzing questions for this exact NEAR MPC target:

    {target_file}

    Use live context from the project if available: the MPC node coordinator, NEAR indexer, request routing, signer contract flows, threshold-signature protocols, triple/presign generation, CKD, participant resharing, RocksDB persistence, P2P networking, chain-gateway, foreign-chain verification, TEE/attestation state, contract threshold-vote logic, and all cross-crate DTO/state conversions.

    Protocol focus:
    This repository contains the production code for NEAR Chain Signatures and the MPC infrastructure used by the NEAR Intents: Bridges HackenProof program. The audit focus is whether an unprivileged contract caller, cross-chain user, malicious foreign-chain input producer, or Byzantine participant below the signing threshold can trigger unauthorized signing, key-share exposure, forged foreign-chain verification, funds theft/freezing, invalid participant state transitions, replay, crash-recovery corruption, idempotency failure, or honest-node divergence.

    Core invariants:

    * Only authorized contract requests, CKD requests, and verified foreign-chain observations may be signed.
    * Below-threshold participants must not be able to force unauthorized signatures, CKD outputs, participant changes, or contract state transitions.
    * Key shares, presignatures, triples, transcript-bound messages, and other sensitive MPC state must never leak, be reused across incompatible contexts, or become forgeable through malformed inputs.
    * Contract and node state transitions must preserve request, participant, threshold-vote, key-state, and migration invariants under adversarial ordering, retries, crashes, and restarts.
    * Foreign-chain verification must be deterministic, chain-bound, replay-resistant, and correctly tied to the intended request, chain, payload, and signer set.
    * Honest nodes and contracts must not diverge because of serialization, storage, transcript, attestation, or network message handling bugs in production code.
    * Duplicate delivery, stale reads, partial persistence, retry-after-crash behavior, and leader failover must not create extra authority, skipped validation, or conflicting final state.

    Rules:

    * Treat `File Name:` as the exact file/module.
    * Treat `Scope:` as the ONLY impact to target.
    * Assume full repo context is accessible.
    * Do not ask for code or say anything is missing.
    * Attacker is unprivileged: external contract caller, malicious request submitter, malicious foreign-chain input producer, or Byzantine protocol participant strictly below the signing threshold.
    * Do not rely on threshold-or-higher collusion, validator collusion, NEAR chain reorg/finality failures, leaked keys, trusted operator control, deployment mistakes, social engineering, public-mainnet testing, raw network-level DoS, griefing-only/no-profit issues, RNG-quality complaints, in-memory zeroization complaints, or the documented mock-attestation grace-period exception.
    * Ignore tests, docs, scripts, examples, mocks, benchmarks, backup tooling, devnet/localnet helpers, and non-default feature-only paths.
    * Generate 10 to 20 high-signal questions.
    * Prefer questions that combine at least two layers: contract + node, node + storage, storage + restart, protocol + serialization, foreign verification + signing, or attestation + authorization.
    * At least 70% must be multi-step flow, invariant, fuzz, accounting, threshold, transcript-binding, replay, authorization, serialization, persistence, restart-safety, or cross-module questions.
    * Every question must be testable by PoC, unit test, integration test, e2e test, local private testnet, fuzz test, invariant test, deterministic simulation, or differential/model comparison.
    * Avoid generic checklist questions and repeated root causes.
    * Prefer concrete bug classes over labels: participant-set drift, request/response mix-up, domain-separation mismatch, replay across chains/domains, nonce/presignature reuse, stale threshold-vote state, duplicate submission, inconsistent canonicalization, TOCTOU between verification and signing, crash-recovery double execution, authorization confusion, or persistence rollback.
    * De-prioritize purely stylistic cryptography criticism unless it creates a reachable exploit path in this codebase.
    * Note any question u must target valid issue u think could be possible.

    High-value attack surfaces:

    * Contract lifecycle: `sign`, CKD, pending requests, threshold votes, participant updates, key events, TEE proposals, migrations, and resharing transitions.
    * Node orchestration: request hashing, leader selection, fallback leadership, request routing, contract polling, signature submission, retries, and crash recovery.
    * Threshold cryptography: DKG, resharing, presignature/triple consumption, rerandomization, nonce handling, transcript binding, participant set consistency, and domain separation.
    * Foreign-chain verification: RPC result canonicalization, extractor correctness, whitelist enforcement, replay resistance, chain binding, and signing of observed external state.
    * Storage and networking: keyshare persistence, RocksDB state transitions, authenticated peer handshakes, protocol sub-channel routing, and message replay/order handling.
    * TEE and attestation: verifier votes, measurement updates, report binding, and production attestation checks, excluding the documented mock-attestation grace-period exception.
    * Cross-boundary seams: contract DTO mapping, Borsh/JSON serialization boundaries, migration compatibility, request hashing inputs, chain ID / domain ID propagation, and restart-time reconstruction of in-flight work.

    Impact mapping:

    * Critical: Theft, direct loss, or permanent freezing of funds controlled by the MPC network, chain-signature contract, or verified foreign-chain flow.
    * Critical: Unauthorized transaction execution, threshold signature issuance, or confidential key derivation output without the required participant authorization.
    * Critical: Bypass of threshold-signature requirements or unauthorized access to MPC key shares, signing capability, or secret material that materially enables forgery or secret recovery.
    * High: Cross-chain replay, forged foreign-chain verification, light-client-style verification bypass, or participant/attestation authorization bypass that causes invalid bridge execution or double-spend conditions.
    * Medium: Balance, request-lifecycle, participant-state, or contract execution-flow manipulation that breaks production safety/accounting invariants without relying on network-level DoS or operator misconfiguration.

    Question quality bar:

    * Ask questions that a strong auditor would actually spend time on after reading the code, not questions that can be answered by one obvious guard.
    * Prefer "what exact invariant breaks if these two modules disagree?" over "is there a bug in X?".
    * Prefer exploit paths with attacker-controlled bytes, ordering, retries, or participant behavior over vague misuse scenarios.
    * If a class is likely impossible because of threshold assumptions or explicit binding, move on to a more realistic class.

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
    "[File: {target_file}] [Function: symbol_or_module] Can an unprivileged ATTACKER_ACTION under PRECONDITIONS trigger CALL_SEQUENCE, causing MODULE_A and MODULE_B to disagree about INVARIANT, leading to scoped impact: SCOPE_IMPACT? Proof idea: build a deterministic unit/integration/fuzz/state test that drives PARAMETERS, models the attacker-controlled ordering or bytes, and asserts EXPECTED_PROPERTY.",
    ]
    """
    return prompt


def audit_format(question: str) -> str:
    """
    Generate a focused NEAR MPC exploit-question validation prompt.
    """
    return f"""# QUESTION SCAN PROMPT

## Exploit Question
{question}

## Scope Rules
- Audit only production NEAR MPC code covered by the live HackenProof NEAR Intents: Bridges program: https://hackenproof.com/programs/near-intents-bridges
- Do not ask for repo contents or claim files are missing.
- Ignore tests, docs, mocks, scripts, configs, build files, IDE files, package metadata, vendored libraries, snapshot files, local-only fixtures, backup tooling, devnet/localnet helpers, and non-default feature-only paths.

## Objective
Decide whether the question leads to a real, reachable NEAR MPC vulnerability.
The attacker must be unprivileged and enter through contract requests, CKD requests, foreign-chain verification inputs, protocol messages, serialized inputs, restart/retry ordering, or by Byzantine behavior strictly below the signing threshold.
The impact must match one of the allowed NEAR MPC impacts below.
Prefer #NoVulnerability unless the path is concrete, local-testable, and bounty-grade.

## Allowed Impact Scope
Only these impacts are valid:
- Critical. Theft, direct loss, or permanent freezing of funds controlled by the MPC network, chain-signature contract, or verified foreign-chain flow.
- Critical. Unauthorized transaction execution, threshold signature issuance, or confidential key derivation output without the required participant authorization.
- Critical. Bypass of threshold-signature requirements or unauthorized access to MPC key shares, signing capability, or secret material that materially enables forgery or secret recovery.
- High. Cross-chain replay, forged foreign-chain verification, light-client-style verification bypass, or participant/attestation authorization bypass that causes invalid bridge execution or double-spend conditions.
- Medium. Balance, request-lifecycle, participant-state, or contract execution-flow manipulation that breaks production safety/accounting invariants without relying on network-level DoS or operator misconfiguration.

## Method
1. Trace the attacker-controlled entrypoint.
2. Map it to exact production NEAR MPC files/functions.
3. Check the relevant guard: contract authorization, participant threshold enforcement, transcript binding, key-state transition, foreign-chain verification binding, replay prevention, attestation authorization, storage invariant, request lifecycle invariant, or crash/idempotency invariant.
4. Decide whether the questioned invariant can actually break under intended deployment.
5. Prove root cause with file/function/line references.
6. Confirm realistic likelihood and exact scoped impact.
7. Reject if current validation already prevents the exploit.

## Reject Immediately
- Requires threshold-or-above collusion, validator collusion, NEAR chain reorg/finality failure, leaked keys, trusted operator access, or deployment misconfiguration.
- Requires physical TEE compromise, social engineering, public-mainnet testing, network-level DoS, griefing-only/no-profit harm, or raw unbounded gas/storage consumption.
- Only affects tests, docs, configs, scripts, mocks, snapshots, examples, local fixtures, backup tooling, devnet/localnet helpers, or non-default feature flags.
- Only proves the documented mock-attestation grace-period exception, RNG quality complaints, or in-memory zeroization complaints.
- External dependency behavior is the only cause.
- Impact is only observability, local misconfiguration, harmless reject, stale read, or non-security correctness.
- The issue depends only on a liveness slowdown with no security or state-integrity break.
- No concrete scoped impact or no realistic exploit path.

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
    Generate a short cross-project analog scan prompt for NEAR MPC.
    """
    prompt = f"""# ANALOG SCAN PROMPT

## External Report
{report}

## Access Rules (Strict)
- Treat production NEAR MPC files in the provided scope as accessible context.
- Do not claim missing/inaccessible files.
- Do not ask for repository contents.
- Do not scan tests, docs, build files, IDE files, configs, resources, snapshot files, local fixtures, backup tooling, devnet/localnet helpers, or package metadata as audited targets.

## Objective
Use the external report's vulnerability class as a hint to find valid issues based on the NEAR Intents: Bridges HackenProof scope for NEAR MPC.
Focus on reachable issues triggered by an unprivileged contract caller, malicious foreign-chain input producer, adversarial restart/retry conditions, or Byzantine participant strictly below the signing threshold.
Only report an analog if this codebase has its own reachable root cause and the impact matches one of the allowed NEAR MPC impacts below.

## Allowed Impact Scope
Only these impacts are valid:
- Critical. Theft, direct loss, or permanent freezing of funds controlled by the MPC network, chain-signature contract, or verified foreign-chain flow.
- Critical. Unauthorized transaction execution, threshold signature issuance, or confidential key derivation output without the required participant authorization.
- Critical. Bypass of threshold-signature requirements or unauthorized access to MPC key shares, signing capability, or secret material that materially enables forgery or secret recovery.
- High. Cross-chain replay, forged foreign-chain verification, light-client-style verification bypass, or participant/attestation authorization bypass that causes invalid bridge execution or double-spend conditions.
- Medium. Balance, request-lifecycle, participant-state, or contract execution-flow manipulation that breaks production safety/accounting invariants without relying on network-level DoS or operator misconfiguration.

## Method
1. Classify vuln type: unauthorized signing, threshold bypass, key-share/state disclosure, replay/verification bypass, request/accounting/state corruption, crash-recovery/idempotency failure, or honest-node divergence.
2. Map to NEAR MPC components and exact production files.
3. Prove root cause with exact file/function/module/line references.
4. Confirm concrete scoped impact and realistic likelihood.
5. Explain the attacker-controlled entry path and why this repository's code is a necessary vulnerable step.
6. Reject if the impact does not match one of the allowed NEAR MPC impacts above.

## Disqualify Immediately
- No reachable attacker-controlled entry path.
- Requires threshold-or-above collusion, validator collusion, trusted role, leaked key, or privileged operator access.
- Requires physical TEE attacks, social engineering, public-mainnet testing, network-level DoS, griefing-only/no-profit harm, or raw unbounded gas/storage complaints.
- Only proves the mock-attestation grace-period exception, RNG quality complaints, or in-memory zeroization complaints.
- External dependency behavior is the only cause.
- Test/docs/config/build/devnet/localnet/example-only issue.
- Theoretical-only issue with no protocol impact.
- The issue disappears once realistic restart, persistence, or threshold assumptions are applied.
- Impact is only local misconfiguration, observability noise, harmless reject, stale read, or non-security correctness.
- Impact or likelihood missing.

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
    Generate a strict NEAR MPC bounty-style validation prompt for security claims.
    """
    prompt = f"""# VALIDATION PROMPT

## Security Claim
{report}

## Rules
- Validate only the submitted claim.
- Check the live HackenProof NEAR Intents: Bridges program for scope, exclusions, and valid severity: https://hackenproof.com/programs/near-intents-bridges
- Do not create a new vulnerability if the submitted claim is weak or invalid.
- Do not upgrade severity unless the provided evidence proves the higher impact.
- Reject admin-only, threshold-or-above-collusion, validator-collusion, trusted-operator, leaked-key, deployment-only, docs/style, config/build-only, fee-only, and purely theoretical issues.
- Reject if the exploit requires unrealistic assumptions, victim mistakes, social engineering, physical attacks on TEE hardware, public-mainnet DoS testing, raw volumetric DDoS, missing external context, or unsupported protocol behavior.
- A valid report must be triggerable by an unprivileged user or by a Byzantine protocol participant strictly below the signing threshold, unless the claim proves privilege escalation from an unprivileged path.
- The final impact must match an in-scope bounty impact, not just a generic code bug.
- Reject any issue whose final impact is not one of the allowed NEAR MPC impacts listed below.
- Prefer #NoVulnerability over speculative reports.
- Prefer concrete state-machine or trust-boundary failures over generic "crypto looks risky" claims.

## In-Scope Protocol Areas
The claim must affect production in-scope NEAR MPC code or systems, such as:
- Contract flows: request creation, signing authorization, pending requests, threshold votes, participant changes, key events, CKD, TEE state, migrations, resharing, and updates.
- Node flows: indexer ingestion, request routing, leader selection, fallback leadership, signature submission, queueing, persistence, keyshare management, migration service, and authenticated peer communication.
- Threshold protocols: DKG, resharing, presignature generation, triple generation, signing, rerandomization, transcript binding, and CKD domain separation.
- Foreign-chain verification: deterministic RPC extraction, chain binding, replay prevention, whitelist enforcement, and signature generation over verified observations.
- Serialization and storage: contract DTOs, state encoding, RocksDB persistence, migration compatibility, and message/attestation/report serialization.
- Cross-boundary consistency: request hashing, chain/domain propagation, participant-set reconstruction, failover handling, and replay/idempotency after retries or restarts.

Reject tests, docs, examples, mocks, generated files, snapshot files, backup tooling, devnet/localnet helpers, non-default feature-only code, and issues that only affect local deployment or operations unless the submitted claim proves a direct in-scope security impact.

## Allowed Impact Scope
Only these impacts are valid:
- Critical. Theft, direct loss, or permanent freezing of funds controlled by the MPC network, chain-signature contract, or verified foreign-chain flow.
- Critical. Unauthorized transaction execution, threshold signature issuance, or confidential key derivation output without the required participant authorization.
- Critical. Bypass of threshold-signature requirements or unauthorized access to MPC key shares, signing capability, or secret material that materially enables forgery or secret recovery.
- High. Cross-chain replay, forged foreign-chain verification, light-client-style verification bypass, or participant/attestation authorization bypass that causes invalid bridge execution or double-spend conditions.
- Medium. Balance, request-lifecycle, participant-state, or contract execution-flow manipulation that breaks production safety/accounting invariants without relying on network-level DoS or operator misconfiguration.

Informational, non-security correctness, observability/logging-only, harmless reject/revert, stale read without security impact, local misconfiguration, and non-demonstrably-exploitable reports are invalid for this validation output.

If the submitted claim does not concretely prove one of the allowed NEAR MPC impacts above, it is invalid.

## Required Validation Checks
All must pass:
1. Exact in-scope file, function, and line/code references.
2. Clear root cause and broken security/accounting/authorization/threshold/verification/idempotency assumption.
3. Reachable exploit path: preconditions -> attacker action -> trigger -> bad result.
4. Existing checks/guards reviewed and shown insufficient.
5. Concrete impact that exactly matches one allowed NEAR MPC impact above, with realistic likelihood.
6. Reproducible safe proof path: unit PoC, local private testnet, deterministic integration test, invariant/fuzz test, simulation, or differential/model-comparison test.
7. No obvious rejection reason from the live program rules, known issues, privileges, or scope exclusions.

## Silent Triage Questions
Before output, internally answer:
- Can a normal external user or below-threshold Byzantine protocol participant trigger this?
- Does the code actually behave as claimed?
- Is the impact caused by NEAR MPC production code, not by an external dependency alone?
- Is the signing/key-share/funds/replay/verification/state impact concrete, not hypothetical?
- Does the issue still hold after accounting for crash recovery, retries, leader failover, and persistent-state reconstruction?
- Does the claim avoid threshold-collusion, validator-collusion, operator-misconfiguration, mainnet DoS, and physical-TEE assumptions?
- Would a bounty triager accept the proof?
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
[Concrete allowed NEAR MPC bounty impact and severity rationale]

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
