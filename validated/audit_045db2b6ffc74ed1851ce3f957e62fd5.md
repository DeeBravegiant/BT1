Audit Report

## Title
Uninitialized `tokenFeeBps` Mapping Causes Zero-Fee Token Deposits - (File: contracts/pools/RSETHPool.sol)

## Summary
`RSETHPool.sol` maintains a per-token fee mapping `tokenFeeBps` that is read by the token deposit path but is never written during token registration via `addSupportedToken`. Every token added to the pool defaults to a fee rate of zero, causing the protocol to collect no fee revenue on token deposits until an admin separately calls `setTokenFeeBps`. The ETH deposit path is unaffected and charges the configured `feeBps`.

## Finding Description
`RSETHPool.sol` has two overloaded `viewSwapRsETHAmountAndFee` functions:

**ETH deposit path** (`viewSwapRsETHAmountAndFee(uint256 amount)`, L311–312):
```solidity
fee = amount * feeBps / 10_000;
```
`feeBps` is set at initialization and is non-zero in production.

**Token deposit path** (`viewSwapRsETHAmountAndFee(uint256 amount, address token)`, L335–336):
```solidity
uint256 feeBpsForToken = tokenFeeBps[token];
fee = amount * feeBpsForToken / 10_000;
```
`tokenFeeBps[token]` is a Solidity mapping that defaults to `0`.

**Root cause** — `addSupportedToken` (L637–656) registers the token's oracle and bridge but never writes `tokenFeeBps[token]`:
```solidity
supportedTokenList.push(token);
supportedTokenOracle[token] = oracle;
tokenBridge[token] = bridge;
// tokenFeeBps[token] is never set
emit AddSupportedToken(token, oracle, bridge);
```

A separate setter `setTokenFeeBps` (L583–594) exists but is not called atomically during token registration and is not enforced or prompted by the contract. The window from token addition until an admin separately calls `setTokenFeeBps` is unbounded.

The `deposit(address token, uint256 amount, string referralId)` function (L284–305) calls `viewSwapRsETHAmountAndFee(amount, token)` and accumulates `feeEarnedInToken[token] += fee`, which will be `0` for any token whose `tokenFeeBps` has not been explicitly set.

**Note on other contracts cited in the submission:** `RSETHPoolNoWrapper`, `RSETHPoolV3`, `RSETHPoolV3ExternalBridge`, and `RSETHPoolV3WithNativeChainBridge` do **not** share this specific bug. Their token deposit paths use the global `feeBps` variable (e.g., `RSETHPoolV3.sol` L324: `fee = amount * feeBps / 10_000`), not a per-token mapping. The uninitialized-mapping defect is confined to `RSETHPool.sol`.

## Impact Explanation
Every token deposit made before an admin explicitly calls `setTokenFeeBps` yields `fee = 0`. The protocol treasury receives no fee revenue from the token deposit path. This constitutes a permanent loss of unclaimed yield for the protocol on all token volume processed through `RSETHPool` before the fee is configured.

**Impact: High — Theft of unclaimed yield.** The protocol's intended fee revenue on token deposits is not collected; depositors receive rsETH at a 0% fee rate instead of the configured rate.

## Likelihood Explanation
The zero-fee state is the default for every newly added token. No special privileges, conditions, or attacker action are required — any unprivileged depositor calling `deposit(token, amount, referralId)` while `tokenFeeBps[token] == 0` pays zero fee. The window lasts from token addition until an admin separately calls `setTokenFeeBps`, which is not enforced or prompted by the contract. This is immediately exploitable upon token listing.

**Likelihood: High.**

## Recommendation
Add a `_feeBps` parameter to `addSupportedToken` and write it atomically during token registration:

```solidity
function addSupportedToken(
    address token,
    address oracle,
    address bridge,
    uint256 _feeBps
) external onlyRole(TIMELOCK_ROLE) {
    if (_feeBps > 10_000) revert InvalidFeeAmount();
    // ... existing checks ...
    supportedTokenList.push(token);
    supportedTokenOracle[token] = oracle;
    tokenBridge[token] = bridge;
    tokenFeeBps[token] = _feeBps;   // initialize atomically
    emit AddSupportedToken(token, oracle, bridge);
}
```

This mirrors how `feeBps` is set for ETH at initialization and eliminates the inconsistency between the two deposit paths.

## Proof of Concept
1. Admin calls `addSupportedToken(wstETH, oracle, bridge)` — `tokenFeeBps[wstETH]` is `0` (Solidity default).
2. User calls `deposit(wstETH, 10 ether, "ref")`.
3. `viewSwapRsETHAmountAndFee(10 ether, wstETH)` executes: `feeBpsForToken = tokenFeeBps[wstETH] = 0`, `fee = 10 ether * 0 / 10_000 = 0`.
4. `feeEarnedInToken[wstETH] += 0` — protocol collects nothing.
5. The same user depositing `10 ether` of native ETH via `deposit("ref")` would pay `10 ether * feeBps / 10_000` (e.g., 5 bps → 0.005 ETH).
6. The two paths are inconsistent; all token volume is fee-free until an admin intervenes.

**Foundry test plan:**
```solidity
function test_tokenDepositZeroFee() public {
    // Setup: deploy RSETHPool, add wstETH via addSupportedToken (no feeBps param)
    // Assert: tokenFeeBps[wstETH] == 0
    // Action: user deposits 10e18 wstETH
    // Assert: feeEarnedInToken[wstETH] == 0
    // Assert: ETH deposit of same value yields fee > 0
}
``` [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5)

### Citations

**File:** contracts/pools/RSETHPool.sol (L88-88)
```text
    mapping(address token => uint256 feeBps) public tokenFeeBps;
```

**File:** contracts/pools/RSETHPool.sol (L284-305)
```text
    function deposit(
        address token,
        uint256 amount,
        string memory referralId
    )
        external
        nonReentrant
        whenNotPaused
        onlySupportedToken(token)
    {
        if (amount == 0) revert InvalidAmount();

        IERC20(token).safeTransferFrom(msg.sender, address(this), amount);

        (uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount, token);

        feeEarnedInToken[token] += fee;

        IERC20(address(wrsETH)).safeTransfer(msg.sender, rsETHAmount);

        emit SwapOccurred(msg.sender, rsETHAmount, fee, referralId); // Add token address?
    }
```

**File:** contracts/pools/RSETHPool.sol (L311-312)
```text
    function viewSwapRsETHAmountAndFee(uint256 amount) public view returns (uint256 rsETHAmount, uint256 fee) {
        fee = amount * feeBps / 10_000;
```

**File:** contracts/pools/RSETHPool.sol (L335-336)
```text
        uint256 feeBpsForToken = tokenFeeBps[token];
        fee = amount * feeBpsForToken / 10_000;
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
