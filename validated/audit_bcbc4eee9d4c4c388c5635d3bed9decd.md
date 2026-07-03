### Title
Newly Added Tokens Default to Zero Fee in `RSETHPool`, Allowing Fee-Free Deposits — (File: contracts/pools/RSETHPool.sol)

### Summary
`RSETHPool.sol` maintains two distinct fee variables: a global `feeBps` for ETH deposits and a per-token `tokenFeeBps[token]` for ERC-20 token deposits. Because `tokenFeeBps[token]` is a mapping that defaults to `0` and is never initialized inside `addSupportedToken`, any token added to the pool carries a zero fee until the admin separately calls `setTokenFeeBps`. Any user who deposits that token during this window — or permanently if the admin never sets the fee — pays no fee at all, while ETH depositors continue to pay `feeBps`. This is the direct analog of the reference bug: the wrong fee value (0 instead of `feeBps`) is applied to a specific operation (token deposit).

### Finding Description
`RSETHPool.sol` exposes two overloaded `deposit` paths:

1. **ETH deposit** — calls `viewSwapRsETHAmountAndFee(amount)`, which charges `feeBps`: [1](#0-0) 

2. **Token deposit** — calls `viewSwapRsETHAmountAndFee(amount, token)`, which charges `tokenFeeBps[token]`: [2](#0-1) 

`tokenFeeBps` is a plain mapping; its default value for any key is `0`. The `addSupportedToken` function registers the token's oracle and bridge but never initialises `tokenFeeBps[token]`: [3](#0-2) 

The setter exists but is a separate, optional call: [4](#0-3) 

Because `fee = amount * 0 / 10_000 = 0`, the `feeEarnedInToken[token]` accumulator is never incremented, and the user receives the full token-equivalent rsETH amount with no fee deducted: [5](#0-4) 

### Impact Explanation
Every token deposit made while `tokenFeeBps[token] == 0` pays zero protocol fee. The fee revenue that should accrue to the protocol (and ultimately to fee recipients via `withdrawFees`) is permanently lost for those deposits. This constitutes **theft of unclaimed yield** (High severity per the allowed scope). The magnitude scales with deposit volume; for a high-throughput pool this can be substantial.

### Likelihood Explanation
The condition is triggered automatically the moment any token is added via `addSupportedToken`. No special attacker setup is required — any ordinary depositor calling `deposit(token, amount, referralId)` benefits from the zero fee. The window persists until the admin separately calls `setTokenFeeBps`, which is not enforced or atomically coupled to token addition. Given that governance/timelock operations are often batched and the fee setter is a distinct transaction, a non-trivial gap is realistic on every new token listing.

### Recommendation
Initialize `tokenFeeBps[token]` to `feeBps` (or a caller-supplied value) inside `addSupportedToken`, so that a newly listed token inherits the global fee rate by default:

```solidity
function addSupportedToken(address token, address oracle, address bridge) external onlyRole(TIMELOCK_ROLE) {
    // ... existing checks ...
    supportedTokenList.push(token);
    supportedTokenOracle[token] = oracle;
    tokenBridge[token] = bridge;
    tokenFeeBps[token] = feeBps;   // ← initialize to global fee
    emit AddSupportedToken(token, oracle, bridge);
}
```

Alternatively, fall back to `feeBps` inside `viewSwapRsETHAmountAndFee(amount, token)` when `tokenFeeBps[token]` is zero.

### Proof of Concept
1. Admin calls `addSupportedToken(tokenA, oracleA, bridgeA)`. `tokenFeeBps[tokenA]` is `0`.
2. Attacker (or any user) immediately calls `deposit(tokenA, 1_000e18, "")`.
3. `viewSwapRsETHAmountAndFee(1_000e18, tokenA)` computes `fee = 1_000e18 * 0 / 10_000 = 0`.
4. User receives the full rsETH equivalent; `feeEarnedInToken[tokenA]` remains `0`.
5. Compare with an ETH deposit of equivalent value: `fee = amount * feeBps / 10_000 > 0`.
6. Protocol collects zero fee on the token deposit, losing yield that should have been distributed to fee recipients.

### Citations

**File:** contracts/pools/RSETHPool.sol (L296-304)
```text
        IERC20(token).safeTransferFrom(msg.sender, address(this), amount);

        (uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount, token);

        feeEarnedInToken[token] += fee;

        IERC20(address(wrsETH)).safeTransfer(msg.sender, rsETHAmount);

        emit SwapOccurred(msg.sender, rsETHAmount, fee, referralId); // Add token address?
```

**File:** contracts/pools/RSETHPool.sol (L311-313)
```text
    function viewSwapRsETHAmountAndFee(uint256 amount) public view returns (uint256 rsETHAmount, uint256 fee) {
        fee = amount * feeBps / 10_000;
        uint256 amountAfterFee = amount - fee;
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
