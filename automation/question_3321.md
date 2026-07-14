# Q3321: rollback_to_block mixes fork state across rollback or peak changes

## Question
Can an unprivileged attacker reach wallet RPC or wallet sync flow reaching `rollback_to_block` and control peak announcements, fork shapes, rollback order, and block delivery timing so that `WalletCoinStore.rollback_to_block` in `chia/wallet/wallet_coin_store.py` executes a path where interleave peak changes and rollback-sensitive inputs so `rollback_to_block` mixes fork-specific state across chains, violating the invariant that fork-local state must not leak across rollback and reorg boundaries and leading to Consensus divergence, deterministic validation mismatch, invalid block or spend acceptance, forged weight proof trust, or chain halt caused by an unprivileged block, spend bundle, protocol message, sync path, or mempool interaction?

## Target
- File/function: chia/wallet/wallet_coin_store.py:331 `WalletCoinStore.rollback_to_block`
- Entrypoint: wallet RPC or wallet sync flow reaching `rollback_to_block`
- Attacker controls: peak announcements, fork shapes, rollback order, and block delivery timing
- Exploit idea: interleave peak changes and rollback-sensitive inputs so `rollback_to_block` mixes fork-specific state across chains
- Invariant to test: fork-local state must not leak across rollback and reorg boundaries
- Expected Immunefi impact: Consensus divergence, deterministic validation mismatch, invalid block or spend acceptance, forged weight proof trust, or chain halt caused by an unprivileged block, spend bundle, protocol message, sync path, or mempool interaction
- Fast validation: script a reorg harness around `chia/wallet/wallet_coin_store.py:rollback_to_block` with interleaved peaks and assert fork-local state never leaks across rollback
