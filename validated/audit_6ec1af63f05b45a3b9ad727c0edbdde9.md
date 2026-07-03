Audit Report

## Title
Zero rsETH Minted on Dust Deposits Due to Integer Division Rounding — (`contracts/pools/RSETHPoolV3.sol`, `contracts/pools/RSETHPoolV3ExternalBridge.sol`)

## Summary
The `deposit` functions in `RSETHPoolV3` and `RSETHPoolV3ExternalBridge` accept ETH or supported tokens from any caller but perform no minimum-output check after computing `rsETHAmount` via integer division. When the deposited amount is small enough that `amountAfterFee * 1e18 / rsETHToETHrate` (ETH path) or `amountAfterFee * tokenToETHRate / rsETHToETHrate` (token path) truncates to zero, the contract silently mints 0 wrsETH to the caller while retaining the deposited assets. The user's funds are permanently absorbed into the pool with no recovery path.

## Finding Description

**ETH deposit path — `RSETHPoolV3.sol` lines 299–308 / `RSETHPoolV3ExternalBridge.sol` lines 418–427:**

```solidity
function viewSwapRsETHAmountAndFee(uint256 amount) public view returns (uint256 rsETHAmount, uint256 fee) {
    fee = amount * feeBps / 10_000;
    uint256 amountAfterFee = amount - fee;
    uint256 rsETHToETHrate = getRate();
    rsETHAmount = amountAfterFee * 1e18 / rsETHToETHrate;   // ← truncates to 0
}
```

`rsETHToETHrate` is the rsETH/ETH exchange rate, which starts at `1e18` and grows monotonically as staking rewards accrue. Once `rsETHToETHrate > 1e18` (e.g., `1.05e18`), any deposit where `amountAfterFee < rsETHToETHrate / 1e18 = 1.05` (i.e., `amountAfterFee = 1 wei`) produces `rsETHAmount = 0`.

**Token deposit path — `RSETHPoolV3.sol` lines 315–335 / `RSETHPoolV3ExternalBridge.sol` lines 433–453:**

```solidity
rsETHAmount = amountAfterFee * tokenToETHRate / rsETHToETHrate;   // ← truncates to 0
```

For any token whose `tokenToETHRate < rsETHToETHrate`, the rounding threshold is `amountAfterFee < rsETHToETHrate / tokenToETHRate`. For a token priced at `tokenToETHRate = 3.3e14` (e.g., a low-value ERC-20), deposits up to `~3181` token units yield `rsETHAmount = 0`.

**Exploit flow:**

1. `rsETHToETHrate` has grown above `1e18` (normal protocol operation).
2. Caller invokes `deposit{value: 1}("")` (1 wei ETH) on `RSETHPoolV3`.
3. `limitDailyMint` modifier computes `rsETHAmount = 0`; `dailyMintAmount += 0` — no revert.
4. `deposit` body checks `amount == 0` → passes (amount is 1, not 0).
5. `viewSwapRsETHAmountAndFee` returns `(0, 0)`.
6. `wrsETH.mint(msg.sender, 0)` executes — caller receives nothing.
7. The 1 wei ETH remains in the pool, is eventually bridged to L1, and is irrecoverable by the depositor.

**Existing guards reviewed and found insufficient:**

- `if (amount == 0) revert InvalidAmount()` — guards only the *input* amount, not the *output* rsETH amount.
- `limitDailyMint` modifier — adds `rsETHAmount` (which is 0) to `dailyMintAmount`; does not revert on zero output.
- No `minRSETHAmountExpected` slippage parameter exists in any L2 pool deposit function (unlike `LRTDepositPool.depositETH` on L1, which has this guard).

## Impact Explanation

**Low — Contract fails to deliver promised returns, but doesn't lose value.**

A depositor who sends a sub-threshold amount receives 0 wrsETH while their ETH/tokens are permanently absorbed into the pool. The protocol retains the assets (no protocol-level loss), but the individual user suffers a complete loss of their deposited amount with no recourse. The amounts involved are tiny (1 wei ETH, or a few cents of token), so this does not rise to Medium/Critical permanent-freeze thresholds.

## Likelihood Explanation

Any unprivileged external caller can trigger this by calling the public `deposit` functions with a sufficiently small amount. No special role, flash loan, or oracle manipulation is required. The condition is met whenever `rsETHToETHrate > 1e18`, which is the normal post-genesis state of the protocol. Likelihood is low in practice because rational users deposit meaningful amounts, but the path is always open and requires no attacker capability beyond sending a transaction.

## Recommendation

Add a zero-output guard immediately after computing `rsETHAmount` in both deposit functions:

```solidity
(uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount);
if (rsETHAmount == 0) revert InvalidAmount();
```

Alternatively, add a `minRsETHAmountExpected` parameter (matching the L1 `LRTDepositPool.depositETH` pattern) so callers can specify their own slippage tolerance, which also protects against oracle-rate fluctuations between quote and execution.

## Proof of Concept

```solidity
// Foundry unit test (fork or local mock)
function test_dustDepositReceivesZeroRsETH() public {
    // Set rsETHToETHrate = 1.05e18 (normal post-genesis value)
    mockOracle.setRate(1.05e18);

    uint256 wrsETHBefore = wrsETH.balanceOf(alice);

    // Alice deposits 1 wei ETH
    vm.prank(alice);
    pool.deposit{value: 1}("ref");

    uint256 wrsETHAfter = wrsETH.balanceOf(alice);

    // Alice received 0 wrsETH but pool absorbed her 1 wei
    assertEq(wrsETHAfter - wrsETHBefore, 0);
    assertEq(address(pool).balance, 1);
}
```

The test demonstrates that `wrsETH.mint(alice, 0)` is called silently, the pool balance increases by 1 wei, and Alice has no mechanism to recover her deposit.