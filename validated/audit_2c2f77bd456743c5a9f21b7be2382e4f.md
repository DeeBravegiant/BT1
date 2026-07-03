### Title
Newly Added Tokens in `RSETHPool` Have Zero Fee by Default, Allowing Depositors to Bypass Protocol Fees - (File: contracts/pools/RSETHPool.sol)

### Summary
`RSETHPool.addSupportedToken()` never initializes `tokenFeeBps[token]`, so it defaults to `0`. Any depositor who calls `deposit(token, amount, referralId)` before the admin separately calls `setTokenFeeBps()` receives rsETH with zero protocol fees deducted, stealing yield that should accrue to the protocol.

### Finding Description
`RSETHPool.sol` maintains a per-token fee mapping:

```solidity
mapping(address token => uint256 feeBps) public tokenFeeBps;
``` [1](#0-0) 

When a new token is added via `addSupportedToken()`, only the oracle and bridge are stored — `tokenFeeBps[token]` is never set:

```solidity
function addSupportedToken(address token, address oracle, address bridge) external onlyRole(TIMELOCK_ROLE) {
    ...
    supportedTokenList.push(token);
    supportedTokenOracle[token] = oracle;
    tokenBridge[token] = bridge;
    emit AddSupportedToken(token, oracle, bridge);
}
``` [2](#0-1) 

The fee for token deposits is computed in `viewSwapRsETHAmountAndFee(amount, token)`:

```solidity
uint256 feeBpsForToken = tokenFeeBps[token];
fee = amount * feeBpsForToken / 10_000;
``` [3](#0-2) 

Because `tokenFeeBps[token]` is `0` by default, `fee` is always `0` for any newly added token. The depositor receives the full `amount` worth of rsETH with no fee deducted:

```solidity
uint256 amountAfterFee = amount - fee;  // == amount
rsETHAmount = amountAfterFee * tokenToETHRate / rsETHToETHrate;
``` [4](#0-3) 

The only remedy is a separate admin call to `setTokenFeeBps()`, which is gated by `DEFAULT_ADMIN_ROLE` (not the timelock), but there is no enforcement that it must be called atomically with or before `addSupportedToken()`:

```solidity
function setTokenFeeBps(address token, uint256 _feeBps)
    external
    onlyRole(DEFAULT_ADMIN_ROLE)
    onlySupportedToken(token)
{
    if (_feeBps > 10_000) revert InvalidFeeAmount();
    tokenFeeBps[token] = _feeBps;
    emit TokenFeeBpsSet(token, _feeBps);
}
``` [5](#0-4) 

The `deposit(token, amount, referralId)` function is open to any caller with no access restriction: [6](#0-5) 

### Impact Explanation
**High — Theft of unclaimed yield.** Every token deposit made while `tokenFeeBps[token] == 0` accrues zero fee to `feeEarnedInToken[token]`. The protocol permanently loses the fee revenue that should have been collected on those deposits. Depositors receive more rsETH than they are entitled to at the protocol's expense.

### Likelihood Explanation
**Medium.** The window opens every time a new token is added via `addSupportedToken()`. Since `addSupportedToken` is behind `TIMELOCK_ROLE`, the pending transaction is visible on-chain before execution, giving sophisticated users advance notice. After execution, the window remains open until the admin calls `setTokenFeeBps()`. Any user monitoring the chain can deposit large amounts during this window. This is a realistic, repeatable scenario each time a new token is onboarded.

### Recommendation
Initialize `tokenFeeBps[token]` inside `addSupportedToken()` by accepting a `_feeBps` parameter, ensuring the fee is set atomically with token registration:

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
    ...
}
```

This eliminates the zero-fee window entirely and mirrors how `feeBps` is set at initialization for ETH deposits.

### Proof of Concept
1. Admin calls `addSupportedToken(wstETH, oracle, bridge)` — `tokenFeeBps[wstETH]` is `0`.
2. Attacker immediately calls `deposit(wstETH, 1_000e18, "")`.
3. `viewSwapRsETHAmountAndFee(1_000e18, wstETH)` computes `fee = 1_000e18 * 0 / 10_000 = 0`.
4. Attacker receives rsETH equivalent to the full `1_000e18` wstETH with no fee deducted.
5. `feeEarnedInToken[wstETH]` remains `0`; the protocol collects nothing.
6. Admin later calls `setTokenFeeBps(wstETH, 30)` — but the fee-free deposits already occurred.

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

**File:** contracts/pools/RSETHPool.sol (L335-336)
```text
        uint256 feeBpsForToken = tokenFeeBps[token];
        fee = amount * feeBpsForToken / 10_000;
```

**File:** contracts/pools/RSETHPool.sol (L337-346)
```text
        uint256 amountAfterFee = amount - fee;

        // rate of rsETH in ETH
        uint256 rsETHToETHrate = getRate();

        // rate of token in ETH
        uint256 tokenToETHRate = IOracle(supportedTokenOracle[token]).getRate();

        // Calculate the final rsETH amount
        rsETHAmount = amountAfterFee * tokenToETHRate / rsETHToETHrate;
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
