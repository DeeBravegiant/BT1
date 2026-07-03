Audit Report

## Title
Uninitialized `tokenFeeBps` Causes Zero Fees on All Token Deposits - (File: contracts/pools/RSETHPool.sol)

## Summary
`addSupportedToken` never initializes `tokenFeeBps[token]`, leaving it at the Solidity default of `0` for every newly added token. As a result, `viewSwapRsETHAmountAndFee(amount, token)` always computes `fee = 0`, and every token depositor receives the full rsETH equivalent of their deposit with no fee deducted. The protocol's `feeEarnedInToken[token]` accumulator remains permanently at zero, and all intended fee yield on token deposits is instead transferred to depositors as excess wrsETH.

## Finding Description
`RSETHPool` maintains two separate fee variables: `feeBps` (for ETH deposits, set during `initialize`) and `tokenFeeBps[token]` (for ERC-20 deposits). The `addSupportedToken` function at L637–656 registers `supportedTokenOracle[token]` and `tokenBridge[token]` but never writes to `tokenFeeBps[token]`:

```solidity
// L651-653 — tokenFeeBps[token] is never set here
supportedTokenList.push(token);
supportedTokenOracle[token] = oracle;
tokenBridge[token] = bridge;
```

`viewSwapRsETHAmountAndFee(uint256, address)` at L335–336 reads this uninitialized mapping:

```solidity
uint256 feeBpsForToken = tokenFeeBps[token]; // always 0
fee = amount * feeBpsForToken / 10_000;      // always 0
```

The `deposit(address, uint256, string)` function at L298–300 unconditionally uses this result:

```solidity
(uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount, token);
feeEarnedInToken[token] += fee; // += 0
IERC20(address(wrsETH)).safeTransfer(msg.sender, rsETHAmount); // full amount
```

The only way to set a non-zero fee is via `setTokenFeeBps` at L583–594, which requires `DEFAULT_ADMIN_ROLE` and is a separate, non-atomic call. The `onlySupportedToken` modifier (L100–103) only checks that `supportedTokenOracle[token] != address(0)` — it does not verify that a fee has been configured. There is no guard anywhere that prevents a deposit when `tokenFeeBps[token] == 0`.

## Impact Explanation
**High — Theft of unclaimed yield.**

The protocol is architecturally designed to collect fees on token deposits: `feeEarnedInToken`, `withdrawFees(address, address)`, and `setTokenFeeBps` all exist for this purpose. Because `tokenFeeBps[token]` is always `0`, every depositor receives the full wrsETH equivalent of their token deposit — the fee portion that should be retained by the protocol is instead transferred to the depositor as excess wrsETH. `feeEarnedInToken[token]` never accumulates, and `withdrawFees` for any token yields nothing. This constitutes ongoing theft of the protocol's intended fee yield on all token deposit volume.

## Likelihood Explanation
**High.** The condition is triggered automatically by every call to `deposit(token, amount, referralId)` after `addSupportedToken` is called. No special knowledge, timing, privileged access, or unusual conditions are required. On Arbitrum, where wstETH is the canonical supported token and token deposits are the primary use case, this affects all deposit volume by default.

## Recommendation
Initialize `tokenFeeBps[token]` inside `addSupportedToken`, requiring the caller to supply a fee parameter:

```diff
function addSupportedToken(
    address token,
    address oracle,
-   address bridge
+   address bridge,
+   uint256 _feeBps
) external onlyRole(TIMELOCK_ROLE) {
    ...
+   if (_feeBps > 10_000) revert InvalidFeeAmount();
+   tokenFeeBps[token] = _feeBps;
    ...
}
```

Alternatively, add a guard in `viewSwapRsETHAmountAndFee` or `deposit` that reverts when `tokenFeeBps[token] == 0`, preventing deposits until the admin explicitly configures the fee via `setTokenFeeBps`.

## Proof of Concept
1. Admin calls `addSupportedToken(wstETH, oracle, bridge)` — `tokenFeeBps[wstETH]` is `0` (Solidity default); `setTokenFeeBps` is never called.
2. Any user calls `deposit(wstETH, 100e18, "")`.
3. `viewSwapRsETHAmountAndFee(100e18, wstETH)` computes: `feeBpsForToken = 0`, `fee = 0`, `amountAfterFee = 100e18`.
4. User receives wrsETH equivalent to the full `100e18` wstETH; `feeEarnedInToken[wstETH] += 0`.
5. Repeat for every deposit — `feeEarnedInToken[wstETH]` remains `0`; `withdrawFees(receiver, wstETH)` transfers nothing.

**Foundry test plan:** Deploy `RSETHPool` with a mock oracle and mock wrsETH. Call `addSupportedToken`. Call `deposit(token, 1e18, "")`. Assert `feeEarnedInToken[token] == 0` and that the depositor received `rsETHAmount` equal to the full unconverted amount (no fee deducted). Then call `setTokenFeeBps(token, 100)`, repeat the deposit, and assert `feeEarnedInToken[token] > 0` — confirming the bug is present before the admin call and absent after. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

**File:** contracts/pools/RSETHPool.sol (L298-304)
```text
        (uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount, token);

        feeEarnedInToken[token] += fee;

        IERC20(address(wrsETH)).safeTransfer(msg.sender, rsETHAmount);

        emit SwapOccurred(msg.sender, rsETHAmount, fee, referralId); // Add token address?
```

**File:** contracts/pools/RSETHPool.sol (L335-337)
```text
        uint256 feeBpsForToken = tokenFeeBps[token];
        fee = amount * feeBpsForToken / 10_000;
        uint256 amountAfterFee = amount - fee;
```

**File:** contracts/pools/RSETHPool.sol (L583-594)
```text
    function setTokenFeeBps(
        address token,
        uint256 _feeBps
    )
        external
        onlyRole(DEFAULT_ADMIN_ROLE)
        onlySupportedToken(token)
    {
        if (_feeBps > 10_000) revert InvalidFeeAmount();
        tokenFeeBps[token] = _feeBps;
        emit TokenFeeBpsSet(token, _feeBps);
    }
```

**File:** contracts/pools/RSETHPool.sol (L637-656)
```text
    function addSupportedToken(address token, address oracle, address bridge) external onlyRole(TIMELOCK_ROLE) {
        UtilLib.checkNonZeroAddress(token);
        UtilLib.checkNonZeroAddress(oracle);
        UtilLib.checkNonZeroAddress(bridge);

        if (supportedTokenOracle[token] != address(0)) {
            revert AlreadySupportedToken();
        }
        if (tokenBridge[token] != address(0)) {
            revert AlreadySupportedToken();
        }
        if (IOracle(oracle).getRate() == 0) {
            revert UnsupportedOracle();
        }
        supportedTokenList.push(token);
        supportedTokenOracle[token] = oracle;
        tokenBridge[token] = bridge;

        emit AddSupportedToken(token, oracle, bridge);
    }
```
