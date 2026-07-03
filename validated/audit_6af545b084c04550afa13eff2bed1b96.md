### Title
Uninitialized `tokenFeeBps` Defaults to Zero, Allowing Fee-Free Token Deposits - (File: contracts/pools/RSETHPool.sol)

---

### Summary
In `RSETHPool.sol`, the `tokenFeeBps` mapping for any newly added token is never initialized by `addSupportedToken`, so it silently defaults to `0`. Any unprivileged depositor can call `deposit(token, amount, referralId)` immediately after a token is listed and receive wrsETH with zero protocol fees charged, stealing fee revenue from the protocol.

---

### Finding Description

`addSupportedToken` registers a new token but never sets `tokenFeeBps[token]`:

```solidity
// contracts/pools/RSETHPool.sol
function addSupportedToken(address token, address oracle, address bridge)
    external onlyRole(TIMELOCK_ROLE)
{
    ...
    supportedTokenList.push(token);
    supportedTokenOracle[token] = oracle;
    tokenBridge[token] = bridge;
    // tokenFeeBps[token] is never written — Solidity mapping default = 0
    emit AddSupportedToken(token, oracle, bridge);
}
``` [1](#0-0) 

The fee calculation for token deposits reads directly from that mapping:

```solidity
function viewSwapRsETHAmountAndFee(uint256 amount, address token)
    public view onlySupportedToken(token)
    returns (uint256 rsETHAmount, uint256 fee)
{
    uint256 feeBpsForToken = tokenFeeBps[token];   // 0 for any new token
    fee = amount * feeBpsForToken / 10_000;         // always 0
    uint256 amountAfterFee = amount - fee;
    ...
}
``` [2](#0-1) 

Because `feeBpsForToken` is `0`, `fee` is always `0` for every deposit of a newly listed token. The `deposit(token, amount, referralId)` function then accumulates `feeEarnedInToken[token] += 0`, so no fee is ever accrued. [3](#0-2) 

The only way to fix this post-listing is a separate admin call to `setTokenFeeBps`: [4](#0-3) 

There is no atomic mechanism that forces the fee to be set in the same transaction as `addSupportedToken`, leaving an exploitable window of arbitrary length.

---

### Impact Explanation

Every deposit of a newly added token during the zero-fee window is a direct theft of protocol fee revenue. The fee is the protocol's only compensation for providing the swap service on this pool; receiving `0` instead of the intended `feeBps` basis points on every deposit is a loss of unclaimed yield for the protocol treasury. Severity: **High — Theft of unclaimed yield**.

---

### Likelihood Explanation

Adding new supported tokens is a routine protocol operation (the contract already lists multiple tokens). The window between `addSuntedToken` and a subsequent `setTokenFeeBps` call is non-zero in any realistic deployment sequence. A monitoring bot or a front-running depositor can detect the `AddSupportedToken` event on-chain and immediately begin depositing large amounts of the new token at zero cost. No special privilege is required beyond being a normal depositor.

---

### Recommendation

Require the fee to be supplied and stored atomically inside `addSupportedToken`:

```solidity
function addSupportedToken(
    address token,
    address oracle,
    address bridge,
    uint256 _feeBps          // <-- add this parameter
) external onlyRole(TIMELOCK_ROLE) {
    if (_feeBps > 10_000) revert InvalidFeeAmount();
    ...
    tokenFeeBps[token] = _feeBps;   // initialize before any deposit is possible
    ...
}
```

This mirrors the remediation in the referenced report: enforce a minimum fee floor so that the fee can never be zero unless the admin explicitly and intentionally sets it to zero.

---

### Proof of Concept

1. Admin calls `addSupportedToken(tokenX, oracle, bridge)` — `tokenFeeBps[tokenX]` is `0`.
2. Attacker observes the `AddSupportedToken` event and immediately calls `deposit(tokenX, 1_000_000e18, "")`.
3. `viewSwapRsETHAmountAndFee(1_000_000e18, tokenX)` returns `fee = 1_000_000e18 * 0 / 10_000 = 0`.
4. Attacker receives the full wrsETH equivalent of `1_000_000e18` tokenX with zero fee deducted.
5. `feeEarnedInToken[tokenX]` remains `0`; the protocol treasury receives nothing.
6. Step 2–5 can be repeated until the admin separately calls `setTokenFeeBps(tokenX, N)`.

### Citations

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
