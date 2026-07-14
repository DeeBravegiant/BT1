# Q3261: new_valid_weight_proof redirects or misbinds Data Layer mirror state

## Question
Can an unprivileged attacker reach wallet RPC or wallet sync flow reaching `new_valid_weight_proof` and control mirror identifiers, store ids, urls, payout-linked state, and pending-root timing so that `WalletBlockchain.new_valid_weight_proof` in `chia/wallet/wallet_blockchain.py` executes a path where make `new_valid_weight_proof` apply mirror-linked state changes to a different store, payout target, or pending-root context, violating the invariant that mirror state changes must bind to the intended store and payout-linked singleton state only and leading to Corruption of coin records, lineage, puzzle ownership, offer/trade settlement state, mempool or hint indexes, wallet sync state, pool membership state, or Data Layer root/store state with direct security impact?

## Target
- File/function: chia/wallet/wallet_blockchain.py:81 `WalletBlockchain.new_valid_weight_proof`
- Entrypoint: wallet RPC or wallet sync flow reaching `new_valid_weight_proof`
- Attacker controls: mirror identifiers, store ids, urls, payout-linked state, and pending-root timing
- Exploit idea: make `new_valid_weight_proof` apply mirror-linked state changes to a different store, payout target, or pending-root context
- Invariant to test: mirror state changes must bind to the intended store and payout-linked singleton state only
- Expected Immunefi impact: Corruption of coin records, lineage, puzzle ownership, offer/trade settlement state, mempool or hint indexes, wallet sync state, pool membership state, or Data Layer root/store state with direct security impact
- Fast validation: exercise add/delete mirror flows around pending-root changes and assert `chia/wallet/wallet_blockchain.py:new_valid_weight_proof` binds every effect to the intended store
