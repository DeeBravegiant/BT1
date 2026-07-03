Audit Report

## Title
Fee Rounds to Zero for Small-Decimal Tokens, Enabling Protocol Fee Evasion - (File: contracts/pools/RSETHPool.sol, contracts/pools/RSETHPoolV3.sol, contracts/agETH/AGETHPoolV3.sol)

## Summary
All three pool contracts compute the protocol fee as `fee = amount * feeBps / 10_000` with no minimum-fee guard. For any whitelisted token with small decimals (e.g., 2–6), a depositor can choose an `amount` such that `amount * feeBps < 10_000`, causing Solidity integer truncation to yield `fee = 0`. The depositor receives the full rsETH/agETH output with zero fee paid to the protocol.

## Finding Description
The fee computation is identical across all three contracts:

- `RSETHPool.sol` L335–337: `fee = amount * feeBpsForToken / 10_000; uint256 amountAfterFee = amount - fee;`
- `RSETHPoolV3.sol` L324–325: `fee = amount * feeBps / 10_000; uint256 amountAfterFee = amount - fee;`
- `AGETHPoolV3.sol` L184–185: `fee = amount * feeBps / 10_000; uint256 amountAfterFee = amount - fee;`

The only deposit guard is `if (amount == 0) revert InvalidAmount()` (RSETHPool.sol L294, RSETHPoolV3.sol L282). There is no check that `fee > 0` when `feeBps > 0`, and no minimum deposit relative to `feeBps`.

`addSupportedToken` in `RSETHPoolV3.sol` (L541–554) validates only that the token address and oracle are non-zero and the oracle returns a non-zero rate. It imposes no restriction on token decimals, making it possible for a TIMELOCK_ROLE admin to whitelist any ERC-20 token, including low-decimal ones.

Once a small-decimal token is whitelisted, any unprivileged depositor can call `deposit(token, amount, ref)` with `amount` below the truncation threshold. The fee accumulator `feeEarnedInToken[token]` receives `0` for every such deposit, and the depositor receives rsETH/agETH computed on the full `amount`.

## Impact Explanation
**Low — Contract fails to deliver promised returns (fee collection), but no user principal is lost.**

The protocol's fee mechanism silently fails for small-decimal tokens: `feeEarnedInToken[token]` is never incremented for sub-threshold deposits, so the protocol treasury receives no fee revenue from those deposits. This matches the allowed impact "Contract fails to deliver promised returns, but doesn't lose value."

## Likelihood Explanation
The exploit is only reachable after a privileged admin whitelists a small-decimal token. The attacker themselves requires no privileged access — any external caller can invoke `deposit` once the precondition exists. For the current primary token set (18-decimal ETH/LST tokens), the truncation threshold is sub-wei and not practically exploitable. Likelihood rises to Low–Medium if USDC (6 dec) or EURS (2 dec) is ever added. The attack is repeatable across arbitrarily many small deposits at negligible cost.

## Recommendation
1. Add a minimum-fee guard in each `viewSwapRsETHAmountAndFee` / `viewSwapAgETHAmountAndFee`:
   ```solidity
   if (feeBps > 0 && fee == 0) revert FeeTooSmall();
   ```
2. Alternatively, enforce a minimum deposit: `require(amount * feeBps >= 10_000)`.
3. Document and enforce in `addSupportedToken` that only tokens with ≥ 18 decimals are accepted, or add a per-token minimum deposit to the whitelist logic.

## Proof of Concept
Assume EURS (2 decimals) is whitelisted in `RSETHPool` with `tokenFeeBps[EURS] = 30`:

1. Attacker calls `deposit(EURS, 333, "ref")`.
2. `viewSwapRsETHAmountAndFee(333, EURS)` computes:
   - `feeBpsForToken = 30`
   - `fee = 333 * 30 / 10_000 = 9990 / 10_000 = 0` (integer truncation)
   - `amountAfterFee = 333 - 0 = 333`
3. `feeEarnedInToken[EURS] += 0` — no fee recorded.
4. Attacker receives rsETH on the full 333 units.
5. Repeating across many deposits drains all fee revenue for that token with zero cost beyond gas.

Foundry fuzz test plan: fuzz `amount` in `[1, 333]` with `feeBps = 30` on a fork where EURS is whitelisted; assert `fee == 0` for all inputs in that range and `feeEarnedInToken[EURS]` remains `0` after N deposits.