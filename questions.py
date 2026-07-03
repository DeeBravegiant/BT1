import json
import os

from decouple import config

# todo: if scope_files is: 500 > 50, 300 > 30 , 100 > 10
MAX_REPO = 30
# todo: the path from https:///github.com/dfinity/ICRC-1
SOURCE_REPO = "jito-foundation/jito-solana"
# todo: the name of the repository
REPO_NAME = "jito-solana"
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
    "accounts-db/src/account_info.rs",
    "accounts-db/src/account_locks.rs",
    "accounts-db/src/account_storage.rs",
    "accounts-db/src/account_storage/stored_account_info.rs",
    "accounts-db/src/account_storage_entry.rs",
    "accounts-db/src/account_storage_reader.rs",
    "accounts-db/src/accounts.rs",
    "accounts-db/src/accounts_cache.rs",
    "accounts-db/src/accounts_db.rs",
    "accounts-db/src/accounts_db/geyser_plugin_utils.rs",
    "accounts-db/src/accounts_file.rs",
    "accounts-db/src/accounts_hash.rs",
    "accounts-db/src/accounts_index.rs",
    "accounts-db/src/accounts_index/account_map_entry.rs",
    "accounts-db/src/accounts_index/accounts_index_storage.rs",
    "accounts-db/src/accounts_index/bucket_map_holder.rs",
    "accounts-db/src/accounts_index/in_mem_accounts_index.rs",
    "accounts-db/src/accounts_index/iter.rs",
    "accounts-db/src/accounts_index/roots_tracker.rs",
    "accounts-db/src/accounts_index/secondary.rs",
    "accounts-db/src/accounts_scan.rs",
    "accounts-db/src/accounts_update_notifier_interface.rs",
    "accounts-db/src/ancestors.rs",
    "accounts-db/src/ancient_append_vecs.rs",
    "accounts-db/src/append_vec.rs",
    "accounts-db/src/append_vec/meta.rs",
    "accounts-db/src/blockhash_queue.rs",
    "accounts-db/src/contains.rs",
    "accounts-db/src/is_loadable.rs",
    "accounts-db/src/is_zero_lamport.rs",
    "accounts-db/src/lib.rs",
    "accounts-db/src/obsolete_accounts.rs",
    "accounts-db/src/partitioned_rewards.rs",
    "accounts-db/src/pubkey_bins.rs",
    "accounts-db/src/read_only_accounts_cache.rs",
    "accounts-db/src/rolling_bit_field.rs",
    "accounts-db/src/rolling_bit_field/iterators.rs",
    "accounts-db/src/sorted_storages.rs",
    "accounts-db/src/stake_rewards.rs",
    "accounts-db/src/storable_accounts.rs",
    "accounts-db/src/utils.rs",
    "accounts-db/src/waitable_condvar.rs",
    "banking-stage-ingress-types/src/lib.rs",
    "bls-cert-verify/src/cert_verify.rs",
    "bls-cert-verify/src/lib.rs",
    "builtins-default-costs/src/lib.rs",
    "builtins/src/core_bpf_migration.rs",
    "builtins/src/lib.rs",
    "builtins/src/prototype.rs",
    "bundle/src/lib.rs",
    "compute-budget-instruction/src/builtin_programs_filter.rs",
    "compute-budget-instruction/src/compute_budget_instruction_details.rs",
    "compute-budget-instruction/src/compute_budget_program_id_filter.rs",
    "compute-budget-instruction/src/instructions_processor.rs",
    "compute-budget-instruction/src/lib.rs",
    "compute-budget/src/compute_budget.rs",
    "compute-budget/src/compute_budget_limits.rs",
    "compute-budget/src/lib.rs",
    "connection-cache/src/client_connection.rs",
    "connection-cache/src/connection_cache.rs",
    "connection-cache/src/lib.rs",
    "connection-cache/src/nonblocking/client_connection.rs",
    "connection-cache/src/nonblocking/mod.rs",
    "core/src/bam_connection.rs",
    "core/src/bam_dependencies.rs",
    "core/src/bam_manager.rs",
    "core/src/banking_simulation.rs",
    "core/src/banking_stage.rs",
    "core/src/banking_stage/committer.rs",
    "core/src/banking_stage/consume_worker.rs",
    "core/src/banking_stage/consumer.rs",
    "core/src/banking_stage/decision_maker.rs",
    "core/src/banking_stage/latest_validator_vote_packet.rs",
    "core/src/banking_stage/progress_tracker.rs",
    "core/src/banking_stage/qos_service.rs",
    "core/src/banking_stage/scheduler_messages.rs",
    "core/src/banking_stage/tpu_to_pack.rs",
    "core/src/banking_stage/transaction_scheduler/bam_receive_and_buffer.rs",
    "core/src/banking_stage/transaction_scheduler/bam_scheduler.rs",
    "core/src/banking_stage/transaction_scheduler/bam_utils.rs",
    "core/src/banking_stage/transaction_scheduler/batch_id_generator.rs",
    "core/src/banking_stage/transaction_scheduler/greedy_scheduler.rs",
    "core/src/banking_stage/transaction_scheduler/in_flight_tracker.rs",
    "core/src/banking_stage/transaction_scheduler/mod.rs",
    "core/src/banking_stage/transaction_scheduler/receive_and_buffer.rs",
    "core/src/banking_stage/transaction_scheduler/scheduler.rs",
    "core/src/banking_stage/transaction_scheduler/scheduler_common.rs",
    "core/src/banking_stage/transaction_scheduler/scheduler_controller.rs",
    "core/src/banking_stage/transaction_scheduler/scheduler_error.rs",
    "core/src/banking_stage/transaction_scheduler/transaction_priority_id.rs",
    "core/src/banking_stage/transaction_scheduler/transaction_state.rs",
    "core/src/banking_stage/transaction_scheduler/transaction_state_container.rs",
    "core/src/banking_stage/vote_packet_receiver.rs",
    "core/src/banking_stage/vote_storage.rs",
    "core/src/banking_stage/vote_worker.rs",
    "core/src/banking_trace.rs",
    "core/src/block_creation_loop.rs",
    "core/src/bls_sigverifier.rs",
    "core/src/bls_sigverify/bls_cert_sigverify.rs",
    "core/src/bls_sigverify/bls_sigverifier.rs",
    "core/src/bls_sigverify/bls_vote_sigverify.rs",
    "core/src/bls_sigverify/errors.rs",
    "core/src/bls_sigverify/mod.rs",
    "core/src/bls_sigverify/utils.rs",
    "core/src/bundle.rs",
    "core/src/bundle_sigverify_stage.rs",
    "core/src/bundle_stage.rs",
    "core/src/bundle_stage/bundle_account_locker.rs",
    "core/src/bundle_stage/bundle_consumer.rs",
    "core/src/bundle_stage/bundle_packet_deserializer.rs",
    "core/src/bundle_stage/bundle_storage.rs",
    "core/src/cluster_info_vote_listener.rs",
    "core/src/cluster_slots_service.rs",
    "core/src/cluster_slots_service/cluster_slots.rs",
    "core/src/cluster_slots_service/slot_supporters.rs",
    "core/src/commitment_service.rs",
    "core/src/completed_data_sets_service.rs",
    "core/src/consensus.rs",
    "core/src/consensus/fork_choice.rs",
    "core/src/consensus/heaviest_subtree_fork_choice.rs",
    "core/src/consensus/latest_validator_votes_for_frozen_banks.rs",
    "core/src/consensus/progress_map.rs",
    "core/src/consensus/tower1_14_11.rs",
    "core/src/consensus/tower1_7_14.rs",
    "core/src/consensus/tower_storage.rs",
    "core/src/consensus/tower_vote_state.rs",
    "core/src/consensus/tree_diff.rs",
    "core/src/consensus/vote_stake_tracker.rs",
    "core/src/cost_update_service.rs",
    "core/src/drop_bank_service.rs",
    "core/src/epoch_specs.rs",
    "core/src/fetch_stage.rs",
    "core/src/forwarding_stage.rs",
    "core/src/forwarding_stage/packet_container.rs",
    "core/src/lib.rs",
    "core/src/multicast_shred_check_service.rs",
    "core/src/next_leader.rs",
    "core/src/optimistic_confirmation_verifier.rs",
    "core/src/packet_bundle.rs",
    "core/src/proxy/auth.rs",
    "core/src/proxy/block_engine_stage.rs",
    "core/src/proxy/fetch_stage_manager.rs",
    "core/src/proxy/mod.rs",
    "core/src/proxy/relayer_stage.rs",
    "core/src/repair/ancestor_hashes_service.rs",
    "core/src/repair/block_id_repair_service.rs",
    "core/src/repair/cluster_slot_state_verifier.rs",
    "core/src/repair/duplicate_repair_status.rs",
    "core/src/repair/malicious_repair_handler.rs",
    "core/src/repair/mod.rs",
    "core/src/repair/outstanding_requests.rs",
    "core/src/repair/packet_threshold.rs",
    "core/src/repair/repair_generic_traversal.rs",
    "core/src/repair/repair_handler.rs",
    "core/src/repair/repair_response.rs",
    "core/src/repair/repair_service.rs",
    "core/src/repair/repair_weight.rs",
    "core/src/repair/repair_weighted_traversal.rs",
    "core/src/repair/request_response.rs",
    "core/src/repair/result.rs",
    "core/src/repair/serve_repair.rs",
    "core/src/repair/serve_repair_service.rs",
    "core/src/repair/standard_repair_handler.rs",
    "core/src/replay_stage.rs",
    "core/src/resource_limits.rs",
    "core/src/result.rs",
    "core/src/sample_performance_service.rs",
    "core/src/scheduler_bindings_server.rs",
    "core/src/shred_fetch_stage.rs",
    "core/src/sigverify.rs",
    "core/src/sigverify_stage.rs",
    "core/src/snapshot_packager_service.rs",
    "core/src/snapshot_packager_service/snapshot_gossip_manager.rs",
    "core/src/staked_nodes_updater_service.rs",
    "core/src/tip_manager.rs",
    "core/src/tip_manager/tip_distribution.rs",
    "core/src/tip_manager/tip_payment.rs",
    "core/src/tpu.rs",
    "core/src/tpu_entry_notifier.rs",
    "core/src/tvu.rs",
    "core/src/unfrozen_gossip_verified_vote_hashes.rs",
    "core/src/validator.rs",
    "core/src/vote_simulator.rs",
    "core/src/voting_service.rs",
    "core/src/warm_quic_cache_service.rs",
    "core/src/window_service.rs",
    "feature-set/src/lib.rs",
    "fee/src/lib.rs",
    "gossip/src/cluster_info.rs",
    "gossip/src/contact_info.rs",
    "gossip/src/crds.rs",
    "gossip/src/crds_data.rs",
    "gossip/src/crds_entry.rs",
    "gossip/src/crds_filter.rs",
    "gossip/src/crds_gossip.rs",
    "gossip/src/crds_gossip_error.rs",
    "gossip/src/crds_gossip_pull.rs",
    "gossip/src/crds_gossip_push.rs",
    "gossip/src/crds_shards.rs",
    "gossip/src/crds_value.rs",
    "gossip/src/deprecated.rs",
    "gossip/src/duplicate_shred.rs",
    "gossip/src/duplicate_shred_handler.rs",
    "gossip/src/duplicate_shred_listener.rs",
    "gossip/src/epoch_slots.rs",
    "gossip/src/epoch_specs.rs",
    "gossip/src/gossip_error.rs",
    "gossip/src/gossip_service.rs",
    "gossip/src/legacy_contact_info.rs",
    "gossip/src/lib.rs",
    "gossip/src/node.rs",
    "gossip/src/ping_pong.rs",
    "gossip/src/protocol.rs",
    "gossip/src/push_active_set.rs",
    "gossip/src/received_cache.rs",
    "gossip/src/restart_crds_values.rs",
    "gossip/src/tlv.rs",
    "gossip/src/weighted_shuffle.rs",
    "jito-protos/src/lib.rs",
    "leader-schedule/src/lib.rs",
    "leader-schedule/src/vote_keyed.rs",
    "ledger/src/ancestor_iterator.rs",
    "ledger/src/bank_forks_utils.rs",
    "ledger/src/bigtable_delete.rs",
    "ledger/src/bigtable_upload.rs",
    "ledger/src/bigtable_upload_service.rs",
    "ledger/src/bit_vec.rs",
    "ledger/src/block_error.rs",
    "ledger/src/blockstore.rs",
    "ledger/src/blockstore/blockstore_purge.rs",
    "ledger/src/blockstore/column.rs",
    "ledger/src/blockstore/error.rs",
    "ledger/src/blockstore_cleanup_service.rs",
    "ledger/src/blockstore_db.rs",
    "ledger/src/blockstore_meta.rs",
    "ledger/src/blockstore_metric_report_service.rs",
    "ledger/src/blockstore_options.rs",
    "ledger/src/blockstore_processor.rs",
    "ledger/src/deshred_transaction_notifier_interface.rs",
    "ledger/src/entry_notifier_interface.rs",
    "ledger/src/entry_notifier_service.rs",
    "ledger/src/genesis_utils.rs",
    "ledger/src/leader_schedule_cache.rs",
    "ledger/src/lib.rs",
    "ledger/src/next_slots_iterator.rs",
    "ledger/src/rooted_slot_iterator.rs",
    "ledger/src/shred.rs",
    "ledger/src/shred/common.rs",
    "ledger/src/shred/filter.rs",
    "ledger/src/shred/merkle.rs",
    "ledger/src/shred/merkle_tree.rs",
    "ledger/src/shred/payload.rs",
    "ledger/src/shred/shred_code.rs",
    "ledger/src/shred/shred_data.rs",
    "ledger/src/shred/traits.rs",
    "ledger/src/shred/wire.rs",
    "ledger/src/shredder.rs",
    "ledger/src/sigverify_shreds.rs",
    "ledger/src/staking_utils.rs",
    "ledger/src/transaction_address_lookup_table_scanner.rs",
    "ledger/src/transaction_balances.rs",
    "ledger/src/use_snapshot_archives_at_startup.rs",
    "poh/src/lib.rs",
    "poh/src/poh_controller.rs",
    "poh/src/poh_recorder.rs",
    "poh/src/poh_service.rs",
    "poh/src/record_channels.rs",
    "poh/src/transaction_recorder.rs",
    "precompiles/src/ed25519.rs",
    "precompiles/src/lib.rs",
    "precompiles/src/secp256k1.rs",
    "precompiles/src/secp256r1.rs",
    "program-runtime/src/cpi.rs",
    "program-runtime/src/deploy.rs",
    "program-runtime/src/execution_budget.rs",
    "program-runtime/src/invoke_context.rs",
    "program-runtime/src/lib.rs",
    "program-runtime/src/loaded_programs.rs",
    "program-runtime/src/loading_task.rs",
    "program-runtime/src/mem_pool.rs",
    "program-runtime/src/memory.rs",
    "program-runtime/src/memory_context.rs",
    "program-runtime/src/program_cache_entry.rs",
    "program-runtime/src/serialization.rs",
    "program-runtime/src/stable_log.rs",
    "program-runtime/src/sysvar_cache.rs",
    "program-runtime/src/vm.rs",
    "programs/system/src/lib.rs",
    "programs/system/src/system_instruction.rs",
    "programs/system/src/system_processor.rs",
    "programs/vote/src/lib.rs",
    "programs/vote/src/vote_processor.rs",
    "programs/vote/src/vote_state/handler.rs",
    "programs/vote/src/vote_state/mod.rs",
    "quic-client/src/lib.rs",
    "quic-client/src/nonblocking/mod.rs",
    "quic-client/src/nonblocking/quic_client.rs",
    "quic-client/src/quic_client.rs",
    "reserved-account-keys/src/lib.rs",
    "rpc/src/cluster_tpu_info.rs",
    "rpc/src/filter.rs",
    "rpc/src/lib.rs",
    "rpc/src/max_slots.rs",
    "rpc/src/optimistically_confirmed_bank_tracker.rs",
    "rpc/src/parsed_token_accounts.rs",
    "rpc/src/rpc.rs",
    "rpc/src/rpc/account_resolver.rs",
    "rpc/src/rpc_cache.rs",
    "rpc/src/rpc_completed_slots_service.rs",
    "rpc/src/rpc_health.rs",
    "rpc/src/rpc_pubsub.rs",
    "rpc/src/rpc_pubsub_service.rs",
    "rpc/src/rpc_service.rs",
    "rpc/src/rpc_subscription_tracker.rs",
    "rpc/src/rpc_subscriptions.rs",
    "rpc/src/slot_status_notifier.rs",
    "rpc/src/transaction_notifier_interface.rs",
    "rpc/src/transaction_status_service.rs",
    "runtime-transaction/src/instruction_data_len.rs",
    "runtime-transaction/src/instruction_meta.rs",
    "runtime-transaction/src/lib.rs",
    "runtime-transaction/src/runtime_transaction.rs",
    "runtime-transaction/src/runtime_transaction/sdk_transactions.rs",
    "runtime-transaction/src/runtime_transaction/transaction_view.rs",
    "runtime-transaction/src/signature_details.rs",
    "runtime-transaction/src/transaction_meta.rs",
    "runtime-transaction/src/transaction_with_meta.rs",
    "runtime/src/account_saver.rs",
    "runtime/src/accounts_background_service.rs",
    "runtime/src/accounts_background_service/pending_snapshot_packages.rs",
    "runtime/src/bank.rs",
    "runtime/src/bank/accounts_lt_hash.rs",
    "runtime/src/bank/address_lookup_table.rs",
    "runtime/src/bank/bank_hash_details.rs",
    "runtime/src/bank/builtins/core_bpf_migration/error.rs",
    "runtime/src/bank/builtins/core_bpf_migration/mod.rs",
    "runtime/src/bank/builtins/core_bpf_migration/source_buffer.rs",
    "runtime/src/bank/builtins/core_bpf_migration/target_bpf_v2.rs",
    "runtime/src/bank/builtins/core_bpf_migration/target_builtin.rs",
    "runtime/src/bank/builtins/core_bpf_migration/target_core_bpf.rs",
    "runtime/src/bank/builtins/mod.rs",
    "runtime/src/bank/check_transactions.rs",
    "runtime/src/bank/entry_bytes_budget.rs",
    "runtime/src/bank/fee_distribution.rs",
    "runtime/src/bank/partitioned_epoch_rewards/calculation.rs",
    "runtime/src/bank/partitioned_epoch_rewards/distribution.rs",
    "runtime/src/bank/partitioned_epoch_rewards/epoch_rewards_hasher.rs",
    "runtime/src/bank/partitioned_epoch_rewards/mod.rs",
    "runtime/src/bank/partitioned_epoch_rewards/sysvar.rs",
    "runtime/src/bank/recent_blockhashes_account.rs",
    "runtime/src/bank/serde_snapshot.rs",
    "runtime/src/bank/sysvar_cache.rs",
    "runtime/src/bank_client.rs",
    "runtime/src/bank_forks.rs",
    "runtime/src/bank_utils.rs",
    "runtime/src/block_component_processor.rs",
    "runtime/src/block_component_processor/vote_reward.rs",
    "runtime/src/block_component_processor/vote_reward/epoch_inflation_account_state.rs",
    "runtime/src/commitment.rs",
    "runtime/src/dependency_tracker.rs",
    "runtime/src/epoch_stakes.rs",
    "runtime/src/genesis_utils.rs",
    "runtime/src/inflation_rewards/mod.rs",
    "runtime/src/inflation_rewards/points.rs",
    "runtime/src/installed_scheduler_pool.rs",
    "runtime/src/leader_schedule_utils.rs",
    "runtime/src/lib.rs",
    "runtime/src/loader_utils.rs",
    "runtime/src/non_circulating_supply.rs",
    "runtime/src/prioritization_fee.rs",
    "runtime/src/prioritization_fee_cache.rs",
    "runtime/src/read_optimized_dashmap.rs",
    "runtime/src/rent_collector.rs",
    "runtime/src/reward_info.rs",
    "runtime/src/serde_snapshot.rs",
    "runtime/src/serde_snapshot/obsolete_accounts.rs",
    "runtime/src/serde_snapshot/status_cache.rs",
    "runtime/src/serde_snapshot/storage.rs",
    "runtime/src/serde_snapshot/types.rs",
    "runtime/src/serde_snapshot/utils.rs",
    "runtime/src/snapshot_bank_utils.rs",
    "runtime/src/snapshot_controller.rs",
    "runtime/src/snapshot_minimizer.rs",
    "runtime/src/snapshot_package.rs",
    "runtime/src/snapshot_package/compare.rs",
    "runtime/src/snapshot_utils.rs",
    "runtime/src/snapshot_utils/snapshot_storage_rebuilder.rs",
    "runtime/src/stake_account.rs",
    "runtime/src/stake_history.rs",
    "runtime/src/stake_utils.rs",
    "runtime/src/stake_weighted_timestamp.rs",
    "runtime/src/stakes.rs",
    "runtime/src/stakes/serde_stakes.rs",
    "runtime/src/static_ids.rs",
    "runtime/src/status_cache.rs",
    "runtime/src/transaction_batch.rs",
    "runtime/src/validated_block_finalization.rs",
    "runtime/src/validated_reward_certificate.rs",
    "runtime/src/vote_sender_types.rs",
    "send-transaction-service/src/lib.rs",
    "send-transaction-service/src/send_transaction_service.rs",
    "send-transaction-service/src/tpu_info.rs",
    "send-transaction-service/src/transaction_client.rs",
    "streamer/src/evicting_sender.rs",
    "streamer/src/lib.rs",
    "streamer/src/msghdr.rs",
    "streamer/src/nonblocking/connection_rate_limiter.rs",
    "streamer/src/nonblocking/mod.rs",
    "streamer/src/nonblocking/qos.rs",
    "streamer/src/nonblocking/quic.rs",
    "streamer/src/nonblocking/simple_qos.rs",
    "streamer/src/nonblocking/stream_throttle.rs",
    "streamer/src/nonblocking/swqos.rs",
    "streamer/src/packet.rs",
    "streamer/src/quic.rs",
    "streamer/src/quic_socket.rs",
    "streamer/src/recvmmsg.rs",
    "streamer/src/sendmmsg.rs",
    "streamer/src/streamer.rs",
    "svm-callback/src/lib.rs",
    "svm-feature-set/src/lib.rs",
    "svm-log-collector/src/lib.rs",
    "svm-measure/src/lib.rs",
    "svm-measure/src/macros.rs",
    "svm-measure/src/measure.rs",
    "svm-timings/src/lib.rs",
    "svm-transaction/src/instruction.rs",
    "svm-transaction/src/lib.rs",
    "svm-transaction/src/message_address_table_lookup.rs",
    "svm-transaction/src/svm_message.rs",
    "svm-transaction/src/svm_message/sanitized_message.rs",
    "svm-transaction/src/svm_message/sanitized_transaction.rs",
    "svm-transaction/src/svm_transaction.rs",
    "svm-transaction/src/svm_transaction/sanitized_transaction.rs",
    "svm-type-overrides/src/lib.rs",
    "svm/src/account_loader.rs",
    "svm/src/account_overrides.rs",
    "svm/src/lib.rs",
    "svm/src/message_processor.rs",
    "svm/src/nonce_info.rs",
    "svm/src/program_loader.rs",
    "svm/src/rent_calculator.rs",
    "svm/src/rollback_accounts.rs",
    "svm/src/transaction_account_state_info.rs",
    "svm/src/transaction_balances.rs",
    "svm/src/transaction_commit_result.rs",
    "svm/src/transaction_execution_result.rs",
    "svm/src/transaction_processing_callback.rs",
    "svm/src/transaction_processing_result.rs",
    "svm/src/transaction_processor.rs",
    "syscalls/src/cpi.rs",
    "syscalls/src/lib.rs",
    "syscalls/src/logging.rs",
    "syscalls/src/mem_ops.rs",
    "syscalls/src/sysvar.rs",
    "tls-utils/src/crypto_provider.rs",
    "tls-utils/src/lib.rs",
    "tls-utils/src/notify_key_update.rs",
    "tls-utils/src/quic_client_certificate.rs",
    "tls-utils/src/skip_client_verification.rs",
    "tls-utils/src/skip_server_verification.rs",
    "tls-utils/src/tls_certificates.rs",
    "transaction-context/src/instruction.rs",
    "transaction-context/src/instruction_accounts.rs",
    "transaction-context/src/lib.rs",
    "transaction-context/src/transaction.rs",
    "transaction-context/src/transaction_accounts.rs",
    "transaction-context/src/vm_addresses.rs",
    "transaction-context/src/vm_slice.rs",
    "transaction-view/src/address_table_lookup_frame.rs",
    "transaction-view/src/bytes.rs",
    "transaction-view/src/instructions_frame.rs",
    "transaction-view/src/lib.rs",
    "transaction-view/src/message_header_frame.rs",
    "transaction-view/src/resolved_transaction_view.rs",
    "transaction-view/src/result.rs",
    "transaction-view/src/sanitize.rs",
    "transaction-view/src/signature_frame.rs",
    "transaction-view/src/static_account_keys_frame.rs",
    "transaction-view/src/transaction_config_frame.rs",
    "transaction-view/src/transaction_data.rs",
    "transaction-view/src/transaction_frame.rs",
    "transaction-view/src/transaction_version.rs",
    "transaction-view/src/transaction_view.rs",
    "turbine/src/addr_cache.rs",
    "turbine/src/broadcast_stage.rs",
    "turbine/src/broadcast_stage/broadcast_duplicates_run.rs",
    "turbine/src/broadcast_stage/broadcast_utils.rs",
    "turbine/src/broadcast_stage/standard_broadcast_run.rs",
    "turbine/src/cluster_nodes.rs",
    "turbine/src/lib.rs",
    "turbine/src/retransmit_stage.rs",
    "turbine/src/sigverify_shreds.rs",
    "turbine/src/xdp_sender.rs",
    "udp-client/src/lib.rs",
    "udp-client/src/nonblocking/mod.rs",
    "udp-client/src/nonblocking/udp_client.rs",
    "udp-client/src/udp_client.rs",
    "unified-scheduler-logic/src/lib.rs",
    "unified-scheduler-pool/src/lib.rs",
    "validator/src/admin_rpc_service.rs",
    "validator/src/bootstrap.rs",
    "validator/src/lib.rs",
    "validator/src/shred_receiver_addresses.rs",
    "version/src/client_ids.rs",
    "version/src/lib.rs",
    "version/src/v1.rs",
    "version/src/v2.rs",
    "version/src/v3.rs",
    "version/src/v4.rs",
    "vote/src/lib.rs",
    "vote/src/vote_account.rs",
    "vote/src/vote_parser.rs",
    "vote/src/vote_state_view.rs",
    "vote/src/vote_state_view/field_frames.rs",
    "vote/src/vote_state_view/frame_v1_14_11.rs",
    "vote/src/vote_state_view/frame_v3.rs",
    "vote/src/vote_state_view/frame_v4.rs",
    "vote/src/vote_state_view/list_view.rs",
    "vote/src/vote_transaction.rs",
    "votor-messages/src/consensus_message.rs",
    "votor-messages/src/fraction.rs",
    "votor-messages/src/lib.rs",
    "votor-messages/src/migration.rs",
    "votor-messages/src/reward_certificate.rs",
    "votor-messages/src/vote.rs",
    "votor/src/commitment.rs",
    "votor/src/common.rs",
    "votor/src/consensus_pool.rs",
    "votor/src/consensus_pool/certificate_builder.rs",
    "votor/src/consensus_pool/parent_ready_tracker.rs",
    "votor/src/consensus_pool/slot_stake_counters.rs",
    "votor/src/consensus_pool/vote_pool.rs",
    "votor/src/consensus_pool_service.rs",
    "votor/src/consensus_rewards.rs",
    "votor/src/consensus_rewards/entry.rs",
    "votor/src/consensus_rewards/entry/notar_entry.rs",
    "votor/src/consensus_rewards/entry/partial_cert.rs",
    "votor/src/event.rs",
    "votor/src/event_handler.rs",
    "votor/src/lib.rs",
    "votor/src/root_utils.rs",
    "votor/src/staked_validators_cache.rs",
    "votor/src/timer_manager.rs",
    "votor/src/timer_manager/timers.rs",
    "votor/src/vote_history.rs",
    "votor/src/vote_history_storage.rs",
    "votor/src/voting_service.rs",
    "votor/src/voting_utils.rs",
    "votor/src/votor.rs",

]
target_scopes = [
    "Critical. Network not being able to confirm new transactions (total network shutdown)",
    "Critical. Unintended permanent chain split requiring hard fork (network partition requiring hard fork)",
    "Critical. Direct loss of funds",
    "High. Permanent freezing of funds (fix requires hardfork)",
    "High. Unintended chain split (network partition)",
    "Medium. Temporary freezing of network transactions by delaying one block by 500% or more of the average block time of the preceding 24 hours beyond standard difficulty adjustments",
    "Medium. Causing network processing nodes to process transactions from the mempool beyond set parameters",
    "Medium. Increasing network processing node resource consumption by at least 30% without brute force actions, compared to the preceding 24 hours",
    "Medium. Shutdown of greater than or equal to 30% of network processing nodes without brute force actions, but does not shut down the network",
    "Low. Layer 0/1/2 network bugs that result in unintended smart contract behavior with no concrete funds at direct risk, shutdown of greater than 10% or equal to but less than 30% of network processing nodes without brute force actions but not total network shutdown, or modification of transaction fees outside of design parameters",
]


def question_generator(target_file: str) -> str:
    """
    Generate exploit-focused audit + fuzzing questions for one Jito-Solana protocol target.

    ```
    target_file format:
    "'File Name: runtime/src/bank.rs -> Scope: Critical. Direct loss of funds'"
    ```
    """

    prompt = f"""
    ```

    Generate exploit-focused security audit and fuzzing questions for this exact Jito-Solana protocol target:

    {target_file}

    Use live context from the project if available: Tower BFT and fork choice, replay and bank execution, PoH/shreds/Turbine, QUIC/TPU/TVU packet flow, gossip and repair, blockstore and snapshots, fee/reward accounting, RPC/P2P entrypoints, Jito bundle and block engine integration, parsing, and cryptography.

    Protocol focus:
    This repository builds Jito's Solana validator, covering consensus, replay, execution, shreds, networking, mempool admission, RPC, storage, fee/reward distribution, and block engine integration. The audit focus is whether chain progress can be halted or split, invalid packets/blocks/transactions can be accepted, funds or fees can be lost or frozen, or public protocol entrypoints can trigger bounty-scoped node failures.

    Core invariants:

    * Invalid shreds, repair data, votes, blocks, forks, transactions, or signatures must not be accepted by honest nodes.
    * Valid transactions and blocks must remain processable without unintended chain split, tower lockout breakage, replay divergence, or network halt.
    * Bank, fee, reward, rent, and scheduling logic must preserve balances, fee parameters, and account safety under adversarial ordering and load.
    * TPU, TVU, gossip, repair, QUIC, RPC, and block engine paths must enforce intended admission, limits, and validation under attacker-controlled inputs.
    * Blockstore, shred processing, snapshots, and serialization paths must reject malformed or adversarial inputs safely.
    * Cryptographic verification and hashing must preserve consensus, authorization, and protocol safety.

    Rules:

    * Treat `File Name:` as the exact file/module.
    * Treat `Scope:` as the ONLY impact to target.
    * Assume full repo context is accessible.
    * Do not ask for code or say anything is missing.
    * Attacker is unprivileged: transaction sender, bundle sender, RPC caller, QUIC client, gossip/repair peer, malicious shred/block/input producer, or user of public node APIs.
    * Do not rely on validator operator compromise, leaked keys, malicious supermajority, third-party dependency compromise, Sybil/51% attacks, phishing, spam-only DoS, or public-mainnet testing.
    * Generate 20 to 30 high-signal questions.
    * At least 70% must be multi-step flow, invariant, fuzz, accounting, state-transition, consensus, or cross-module questions.
    * Every question must be testable by PoC, unit test, fuzz test, invariant test, or differential test.
    * Avoid generic checklist questions and repeated root causes.
    * Note any question u must target valid issue u think could be possible

    High-value attack surfaces:

    * Consensus and replay: Tower BFT lockouts, fork choice, vote handling, rooted/frozen bank transitions, optimistic confirmation, duplicate repair, and restart/recovery paths.
    * Transactions and execution: packet admission, transaction scheduling, bundle execution, nonce/fee accounting, compute budgeting, account locking, cost/QoS rules, and runtime checks.
    * State and storage: bank state transitions, fee/reward distribution, blockstore writes/reads, shred assembly/verification, snapshots, and serialization.
    * External entrypoints: JSON-RPC, pubsub, gossip, repair, QUIC/UDP ingress, TPU/TVU paths, send-transaction service, and block engine connectivity.
    * Jito-specific flows: bundles, trusted packet paths, block engine auth/streaming, and validator-side bundle ordering/locking.
    * Cryptography and parsing: vote and shred signatures, packet decoding, protobuf/bincode serialization, hashes, and public key/account serialization.

    Impact mapping:

    * Critical: Network not being able to confirm new transactions (total network shutdown).
    * Critical: Unintended permanent chain split requiring hard fork (network partition requiring hard fork).
    * Critical: Direct loss of funds.
    * High: Permanent freezing of funds (fix requires hardfork).
    * High: Unintended chain split (network partition).
    * Medium: Temporary freezing of network transactions by delaying one block by 500% or more of the average block time of the preceding 24 hours beyond standard difficulty adjustments.
    * Medium: Causing network processing nodes to process transactions from the mempool beyond set parameters.
    * Medium: Increasing network processing node resource consumption by at least 30% without brute force actions, compared to the preceding 24 hours.
    * Medium: Shutdown of greater than or equal to 30% of network processing nodes without brute force actions, but does not shut down the network.
    * Low: Layer 0/1/2 network bugs that result in unintended smart contract behavior with no concrete funds at direct risk, shutdown of greater than 10% or equal to but less than 30% of network processing nodes without brute force actions but not total network shutdown, or modification of transaction fees outside of design parameters.

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
    Generate a focused Jito-Solana protocol exploit-question validation prompt.
    """
    return f"""# QUESTION SCAN PROMPT

## Exploit Question
{question}

## Scope Rules
- Audit only production Jito-Solana protocol code.
- Do not ask for repo contents or claim files are missing.
- Ignore tests, docs, mocks, scripts, configs, build files, IDE files, package metadata, vendored libraries, and local-only fixtures.

## Objective
Decide whether the question leads to a real, reachable Jito-Solana protocol vulnerability.
The attacker must be unprivileged and enter through transaction submission, bundles, RPC, QUIC/UDP, gossip/repair, block/shred/input construction, or a public node API.
The impact must match one of the allowed Jito-Solana protocol impacts below.
Prefer #NoVulnerability unless the path is concrete, local-testable, and bounty-grade.

## Allowed Impact Scope
Only these impacts are valid:
- Critical. Network not being able to confirm new transactions (total network shutdown).
- Critical. Unintended permanent chain split requiring hard fork (network partition requiring hard fork).
- Critical. Direct loss of funds.
- High. Permanent freezing of funds (fix requires hardfork).
- High. Unintended chain split (network partition).
- Medium. Temporary freezing of network transactions by delaying one block by 500% or more of the average block time of the preceding 24 hours beyond standard difficulty adjustments.
- Medium. Causing network processing nodes to process transactions from the mempool beyond set parameters.
- Medium. Increasing network processing node resource consumption by at least 30% without brute force actions, compared to the preceding 24 hours.
- Medium. Shutdown of greater than or equal to 30% of network processing nodes without brute force actions, but does not shut down the network.
- Low. Layer 0/1/2 network bugs that result in unintended smart contract behavior with no concrete funds at direct risk, shutdown of greater than 10% or equal to but less than 30% of network processing nodes without brute force actions but not total network shutdown, or modification of transaction fees outside of design parameters.

## Method
1. Trace the attacker-controlled entrypoint.
2. Map it to exact production Jito-Solana files/functions.
3. Check the relevant guard: consensus or replay validation, shred/signature checks, fee/reward accounting, bank/runtime checks, QoS or mempool limits, block engine trust boundaries, parser bounds, or crypto verification.
4. Decide whether the questioned invariant can actually break under intended deployment.
5. Prove root cause with file/function/line references.
6. Confirm realistic likelihood and exact scoped impact.
7. Reject if current validation already prevents the exploit.

## Reject Immediately
- Requires trusted role, leaked key, malicious supermajority, or privileged operator access.
- Requires third-party dependency compromise, Sybil/51% attack, phishing, public-mainnet testing, or spam-only DoS.
- Only affects tests, docs, configs, scripts, mocks, local fixtures, vendored libraries, or local deployment choices.
- External dependency behavior is the only cause.
- Impact is only logging, observability, local misconfiguration, non-security correctness, harmless revert, stale read, rejected update, or theoretical risk.
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
    Generate a short cross-project analog scan prompt for Jito-Solana protocol.
    """
    prompt = f"""# ANALOG SCAN PROMPT

## External Report
{report}

## Access Rules (Strict)
- Treat production Jito-Solana protocol files in the provided scope as accessible context.
- Do not claim missing/inaccessible files.
- Do not ask for repository contents.
- Do not scan tests, docs, build files, IDE files, configs, resources, local fixtures, vendored libraries, or package metadata as audited targets.

## Objective
Use the external report's vulnerability class as a hint to find valid issues based on the Jito-Solana bounty scope.
Focus on reachable issues triggered by an unprivileged transaction sender, bundle sender, RPC caller, QUIC client, gossip/repair peer, malicious shred/block/input producer, or public node API user.
Only report an analog if this codebase has its own reachable root cause and the impact matches one of the allowed Jito-Solana protocol impacts below.

## Allowed Impact Scope
Only these impacts are valid:
- Critical. Network not being able to confirm new transactions (total network shutdown).
- Critical. Unintended permanent chain split requiring hard fork (network partition requiring hard fork).
- Critical. Direct loss of funds.
- High. Permanent freezing of funds (fix requires hardfork).
- High. Unintended chain split (network partition).
- Medium. Temporary freezing of network transactions by delaying one block by 500% or more of the average block time of the preceding 24 hours beyond standard difficulty adjustments.
- Medium. Causing network processing nodes to process transactions from the mempool beyond set parameters.
- Medium. Increasing network processing node resource consumption by at least 30% without brute force actions, compared to the preceding 24 hours.
- Medium. Shutdown of greater than or equal to 30% of network processing nodes without brute force actions, but does not shut down the network.
- Low. Layer 0/1/2 network bugs that result in unintended smart contract behavior with no concrete funds at direct risk, shutdown of greater than 10% or equal to but less than 30% of network processing nodes without brute force actions but not total network shutdown, or modification of transaction fees outside of design parameters.

## Method
1. Classify vuln type: consensus/replay bypass, chain split, network halt, invalid transaction acceptance, fee/reward accounting bug, shred/blockstore flaw, bundle or mempool control bug, RPC/P2P exploit, parser bounds issue, or crypto verification flaw.
2. Map to Jito-Solana protocol components and exact production files.
3. Prove root cause with exact file/function/module/line references.
4. Confirm concrete scoped impact and realistic likelihood.
5. Explain the attacker-controlled entry path and why this repository's code is a necessary vulnerable step.
6. Reject if the impact does not match one of the allowed Jito-Solana protocol impacts above.

## Disqualify Immediately
- No reachable attacker-controlled entry path.
- Requires trusted role, leaked key, malicious supermajority, or privileged operator access.
- Requires third-party dependency compromise, Sybil/51% attack, phishing, public-mainnet testing, or spam-only DoS.
- External dependency behavior is the only cause.
- Test/docs/config/build-only issue.
- Theoretical-only issue with no protocol impact.
- Impact is only local misconfiguration, observability noise, logging noise, harmless revert, stale read, or non-security correctness.
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
    Generate a strict Jito-Solana protocol bounty-style validation prompt for security claims.
    """
    prompt = f"""# VALIDATION PROMPT

## Security Claim
{report}

## Rules
- Validate only the submitted claim.
- Check SECURITY.md, Researcher.md if present, and the Jito-Solana bounty scope for scope, exclusions, and valid impact classes.
- Do not create a new vulnerability if the submitted claim is weak or invalid.
- Do not upgrade severity unless the provided evidence proves the higher impact.
- Reject admin-only, consensus-threshold/validator-majority corruption, trusted-operator, leaked-key, host-compromise, best-practice, docs/style, config/build-only, gas-fee-only, and purely theoretical issues.
- Reject if the exploit requires unrealistic assumptions, victim mistakes, phishing/social engineering, DNS/BGP hijack, third-party exchange/dapp/oracle compromise, public-mainnet DoS testing, raw volumetric DDoS, missing external context, or unsupported protocol behavior.
- A valid report must be triggerable by an unprivileged user or by a Byzantine protocol peer below the consensus fault threshold, unless the claim proves privilege escalation from an unprivileged path.
- The final impact must match an in-scope bounty impact, not just a generic code bug.
- Reject any issue whose final impact is not one of the allowed Jito-Solana protocol impacts listed below.
- Prefer #NoVulnerability over speculative reports.

## In-Scope Protocol Areas
The claim must affect production in-scope Jito-Solana protocol code or systems, such as:
- Core validator protocol: Tower BFT, fork choice, vote handling, replay, bad-block handling, chain reorg, repair, gossip, shreds, Turbine, QUIC/TPU/TVU, and node APIs.
- Transaction and execution paths: packet admission, transaction scheduling, bundle execution, account locking, nonce/compute/fee checks, mempool limits, runtime execution, and fork-rule enforcement.
- State and storage: bank state transitions, fee/reward distribution, snapshots, blockstore, shred assembly/verification, serialization, and root/hash derivation.
- External protocol entrypoints: JSON-RPC, pubsub, send-transaction service, public client/node APIs, block engine connectivity, and trusted packet flows.
- Cryptography and parsing: vote/shred/transaction signatures, hashes, protobuf/bincode/packet decoding, and account/public-key serialization.

Reject third-party dapps, unlisted public websites, tests, docs, examples, mocks, generated files, local deployment helpers, vendored libraries, and issues that only affect local developer tooling unless the submitted claim proves a direct in-scope Jito-Solana protocol security impact.

## Allowed Impact Scope
Only these impacts are valid:
- Critical. Network not being able to confirm new transactions (total network shutdown).
- Critical. Unintended permanent chain split requiring hard fork (network partition requiring hard fork).
- Critical. Direct loss of funds.
- High. Permanent freezing of funds (fix requires hardfork).
- High. Unintended chain split (network partition).
- Medium. Temporary freezing of network transactions by delaying one block by 500% or more of the average block time of the preceding 24 hours beyond standard difficulty adjustments.
- Medium. Causing network processing nodes to process transactions from the mempool beyond set parameters.
- Medium. Increasing network processing node resource consumption by at least 30% without brute force actions, compared to the preceding 24 hours.
- Medium. Shutdown of greater than or equal to 30% of network processing nodes without brute force actions, but does not shut down the network.
- Low. Layer 0/1/2 network bugs that result in unintended smart contract behavior with no concrete funds at direct risk, shutdown of greater than 10% or equal to but less than 30% of network processing nodes without brute force actions but not total network shutdown, or modification of transaction fees outside of design parameters.

Informational, non-security correctness, observability/logging-only, harmless reject/revert, stale read without consensus/state/accounting/security impact, local misconfiguration, and non-demonstrably-exploitable reports are invalid for this validation output.

If the submitted claim does not concretely prove one of the allowed Jito-Solana protocol impacts above, it is invalid.

## Required Validation Checks
All must pass:
1. Exact in-scope file, function, and line/code references.
2. Clear root cause and broken protocol/security/accounting/authentication/certification assumption.
3. Reachable exploit path: preconditions -> attacker action -> trigger -> bad result.
4. Existing checks/guards reviewed and shown insufficient.
5. Concrete impact that exactly matches one allowed Jito-Solana protocol impact above, with realistic likelihood.
6. Reproducible safe proof path: unit PoC, local private validator network, deterministic integration test, invariant/fuzz test, differential test, or exact local manual steps.
7. No obvious rejection reason from SECURITY.md, Researcher.md if present, known issues, privileges, or scope exclusions.

## Silent Triage Questions
Before output, internally answer:
- Can a normal external user or below-threshold Byzantine protocol peer trigger this?
- Does the code actually behave as claimed?
- Is the impact caused by Jito-Solana production protocol code, not by an external dependency alone?
- Is the consensus/replay/network/funds-loss/accounting impact concrete, not hypothetical?
- Does the claim avoid governance-majority, validator-majority, trusted operator, leaked key, mainnet DoS, and third-party compromise assumptions?
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
[Concrete allowed Jito-Solana protocol bounty impact and severity rationale]

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
