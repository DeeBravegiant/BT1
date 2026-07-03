### Title
Missing `tokenFeeBps` Initialization in `addSupportedToken` Causes Zero Protocol Fees on Token Deposits - (File: `contracts/pools/RSETHPool.sol`)

---

### Summary

`RSETHPool.sol` maintains a per-token fee mapping `tokenFeeBps` that is used to calculate fees on token deposits. However, `addSupportedToken` never initializes `tokenFeeBps[token]`, so it defaults to `0`. Until `setTokenFeeBps` is explicitly called as a separate transaction, the protocol collects zero fees on all deposits of that token.

---

### Finding Description

`RSETHPool` uses two distinct fee variables:

- `feeBps` — applied to native ETH deposits
- `tokenFeeBps[token]` — applied to ERC-20 token deposits [1](#0-0) [2](#0-1) 

The token deposit fee path reads directly from this mapping:

```solidity
uint256 feeBpsForToken = tokenFeeBps[token];
fee = amount * feeBpsForToken / 10_000;
``` [3](#0-2) 

When a new token is added via `addSupportedToken`, the function sets `supportedTokenOracle[token]` and `tokenBridge[token]`, but **never sets `tokenFeeBps[token]`**: [4](#0-3) 

Because Solidity mappings default to `0`, `tokenFeeBps[token]` is `0` for every newly added token. The setter `setTokenFeeBps` exists but is a separate, independent call: [5](#0-4) 

There is no on-chain enforcement that `setTokenFeeBps` must be called after `addSupportedToken`. The protocol will silently collect zero fees on all token deposits until the admin separately remembers to configure the rate.

---

### Impact Explanation

The protocol fails to collect any fees on ERC-20 token deposits for newly added tokens. All deposited token amounts pass through with `fee = 0`, and `feeEarnedInToken[token]` remains `0`. The `withdrawFees(receiver, token)` function would withdraw nothing. This matches the **Low** impact category: *contract fails to deliver promised returns, but doesn't lose user value*. [6](#0-5) 

---

### Likelihood Explanation

Every time a new token is added via `addSupportedToken`, the protocol is silently in a zero-fee state for that token. There is no revert, no event, and no on-chain guard requiring `setTokenFeeBps` to be called. The window of zero-fee collection persists until the admin notices and calls `setTokenFeeBps`. Given that `RSETHPool` is the Arbitrum pool and already has multiple supported tokens, this pattern is likely to recur with each new token addition.

---

### Recommendation

Add a `feeBps` parameter to `addSupportedToken` and initialize `tokenFeeBps[token]` inside it:

```solidity
function addSupportedToken(
    address token,
    address oracle,
    address bridge,
    uint256 _feeBps          // <-- add this
) external onlyRole(TIMELOCK_ROLE) {
    ...
    if (_feeBps > 10_000) revert InvalidFeeAmount();
    tokenFeeBps[token] = _feeBps;
    emit TokenFeeBpsSet(token, _feeBps);
    ...
}
```

---

### Proof of Concept

1. Admin calls `addSupportedToken(tokenA, oracle, bridge)`.
2. `tokenFeeBps[tokenA]` is `0` (Solidity default).
3. A depositor calls `deposit(tokenA, 1_000e18, "ref")`.
4. `viewSwapRsETHAmountAndFee(1_000e18, tokenA)` computes `fee = 1_000e18 * 0 / 10_000 = 0`.
5. `feeEarnedInToken[tokenA] += 0` — no fee is recorded.
6. The depositor receives the full rsETH equivalent of `1_000e18` tokens with no fee deducted.
7. Admin calls `withdrawFees(receiver, tokenA)` — receives `0`. [7](#0-6) [8](#0-7)

### Citations

**File:** contracts/pools/RSETHPool.sol (L43-44)
```text
    uint256 public feeBps; // Basis points for fees for ETH deposits
    uint256 public feeEarnedInETH;
```

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

**File:** contracts/pools/RSETHPool.sol (L427-443)
```text
    /// @dev Withdraws fees earned by the pool
    function withdrawFees(
        address receiver,
        address token
    )
        external
        nonReentrant
        onlySupportedToken(token)
        onlyRole(BRIDGER_ROLE)
    {
        // withdraw fees in ETH
        uint256 amountToSendInToken = feeEarnedInToken[token];
        feeEarnedInToken[token] = 0;
        IERC20(token).safeTransfer(receiver, amountToSendInToken);

        emit FeesWithdrawn(amountToSendInToken, token);
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
