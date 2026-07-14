# Q2377: check_is_did_puzzle accepts stale DID lineage in a live authority path

## Question
Can an unprivileged attacker reach wallet RPC or wallet sync flow reaching `check_is_did_puzzle` and control stale DID parent or lineage state replayed into a live DID path so that `check_is_did_puzzle` in `chia/wallet/did_wallet/did_wallet_puzzles.py` executes a path where make `check_is_did_puzzle` accept stale DID lineage or parent state during a live authority transition, violating the invariant that stale DID parent or lineage state must not authorize live DID actions and leading to Bypass of wallet, daemon, keychain, RPC, pool, or Data Layer authorization that enables unauthorized signing, key use, coin control, payout redirection, singleton mutation, or protected state transitions?

## Target
- File/function: chia/wallet/did_wallet/did_wallet_puzzles.py:192 `check_is_did_puzzle`
- Entrypoint: wallet RPC or wallet sync flow reaching `check_is_did_puzzle`
- Attacker controls: stale DID parent or lineage state replayed into a live DID path
- Exploit idea: make `check_is_did_puzzle` accept stale DID lineage or parent state during a live authority transition
- Invariant to test: stale DID parent or lineage state must not authorize live DID actions
- Expected Immunefi impact: Bypass of wallet, daemon, keychain, RPC, pool, or Data Layer authorization that enables unauthorized signing, key use, coin control, payout redirection, singleton mutation, or protected state transitions
- Fast validation: feed stale parent or lineage data into `chia/wallet/did_wallet/did_wallet_puzzles.py:check_is_did_puzzle` during a live DID update and assert no authority bypass occurs
