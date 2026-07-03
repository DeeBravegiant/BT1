### Title
`tokenFeeBps` Defaults to Zero for Newly Added Tokens, Allowing Fee-Free Token Deposits - (File: contracts/pools/RSETHPool.sol)

### Summary
`RSETHPool.sol` maintains a per-token fee mapping `tokenFeeBps` that defaults to `0` for any token not explicitly configured. When a new supported token is added via `addSupportedToken`, no fee is set atomically. Until a separate `setTokenFeeBps` call is made, any depositor can swap that token for wrsETH with zero protocol fees, permanently losing that fee revenue for the protocol.

### Finding Description

`RSETHPool.sol` declares a per-token fee mapping:

```solidity
mapping(address token => uint256 feeBps) public tokenFeeBps;
``` [1](#0-0) 

When a user calls `deposit(address token, uint256 amount, string referralId)`, the fee is computed as:

```solidity
uint256 feeBpsForToken = tokenFeeBps[token];
fee = amount * feeBpsForToken / 10_000;
``` [2](#0-1) 

If `tokenFeeBps[token]` has never been set, it returns the Solidity default of `0`, making `fee = 0` and `amountAfterFee = amount`. The depositor receives the full rsETH equivalent with no fee deducted. [3](#0-2) 

Fee configuration is a separate, independent admin action:

```solidity
function setTokenFeeBps(address token, uint256 _feeBps)
    external
    onlyRole(DEFAULT_ADMIN_ROLE)
    onlySupportedToken(token)
{
    if (_feeBps > 10_000) revert InvalidFeeAmount();
    tokenFeeBps[token] = _feeBps;
    ...
}
``` [4](#0-3) 

There is no atomic coupling between `addSupportedToken` / `_addSupportedToken` and fee initialization. The token becomes immediately depositable at zero fee the moment it is added.

### Impact Explanation

Every token deposit during the zero-fee window (from token addition until `setTokenFeeBps` is called, or permanently if it is never called) results in the protocol collecting **zero fee revenue** on that swap. Because the pool mints wrsETH to the depositor based on the full `amountAfterFee = amount`, the protocol irreversibly loses the fee it was entitled to. This constitutes **theft of unclaimed yield** (protocol fee revenue) at **High** severity.

### Likelihood Explanation

Likelihood is **Medium**. The window exists every time a new token is added. A sophisticated depositor monitoring the mempool or on-chain events for `AddSupportedToken` can front-run or immediately follow the token-addition transaction with large deposits before `setTokenFeeBps` is called. Additionally, if the admin omits the fee-setting step entirely (an operational error), the zero-fee condition is permanent for that token.

### Recommendation

Set the token fee atomically inside `_addSupportedToken` by requiring a `feeBps` parameter:

```solidity
function _addSupportedToken(address token, address oracle, address bridge, uint256 feeBps) internal {
    // existing checks ...
    supportedTokenOracle[token] = oracle;
    tokenBridge[token] = bridge;
    supportedTokenList.push(token);
    tokenFeeBps[token] = feeBps;   // <-- set fee atomically
    emit AddSupportedToken(token, oracle, bridge);
}
```

This eliminates the zero-fee window entirely and makes fee configuration a mandatory part of token onboarding.

### Proof of Concept

1. Admin calls `addSupportedToken(tokenX, oracleX, bridgeX)` — `tokenFeeBps[tokenX]` is `0`.
2. Attacker immediately calls `deposit(tokenX, 1_000_000e18, "")`.
3. `viewSwapRsETHAmountAndFee(1_000_000e18, tokenX)` computes `fee = 1_000_000e18 * 0 / 10_000 = 0`.
4. Attacker receives wrsETH for the full `1_000_000e18` with no fee paid.
5. Admin later calls `setTokenFeeBps(tokenX, 30)` — but the fee on the prior deposit is already lost. [5](#0-4) [6](#0-5)

### Citations

**File:** contracts/pools/RSETHPool.sol (L87-88)
```text
    /// @dev Mapping of token to fee basis points
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

**File:** contracts/pools/RSETHPool.sol (L326-347)
```text
    function viewSwapRsETHAmountAndFee(
        uint256 amount,
        address token
    )
        public
        view
        onlySupportedToken(token)
        returns (uint256 rsETHAmount, uint256 fee)
    {
        uint256 feeBpsForToken = tokenFeeBps[token];
        fee = amount * feeBpsForToken / 10_000;
        uint256 amountAfterFee = amount - fee;

        // rate of rsETH in ETH
        uint256 rsETHToETHrate = getRate();

        // rate of token in ETH
        uint256 tokenToETHRate = IOracle(supportedTokenOracle[token]).getRate();

        // Calculate the final rsETH amount
        rsETHAmount = amountAfterFee * tokenToETHRate / rsETHToETHrate;
    }
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
