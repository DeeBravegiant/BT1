### Title
Token Deposits in `RSETHPool` Accrue Zero Protocol Fees by Default Due to Uninitialized `tokenFeeBps` Mapping - (File: contracts/pools/RSETHPool.sol)

### Summary
In `RSETHPool.sol` (the Arbitrum L2 pool), token deposit fees are computed from a per-token mapping `tokenFeeBps[token]` that is never initialized when a token is added via `addSupportedToken`. Because Solidity mappings default to zero, every token deposit pays zero fee until the admin separately calls `setTokenFeeBps`. ETH deposits do not share this flaw — `feeBps` is set at construction time. Any depositor using a supported ERC-20 token on Arbitrum therefore receives the full rsETH swap with no fee deducted, permanently depriving the protocol of its intended fee revenue for that token.

### Finding Description
`RSETHPool.viewSwapRsETHAmountAndFee(uint256 amount, address token)` reads the fee rate from `tokenFeeBps[token]`:

```solidity
uint256 feeBpsForToken = tokenFeeBps[token];
fee = amount * feeBpsForToken / 10_000;
```

`tokenFeeBps` is a plain `mapping(address token => uint256 feeBps)`. Its default value for every key is `0`. The `addSupportedToken` function that whitelists new tokens never writes to this mapping:

```solidity
function addSupportedToken(address token, address oracle, address bridge)
    external onlyRole(TIMELOCK_ROLE)
{
    ...
    supportedTokenList.push(token);
    supportedTokenOracle[token] = oracle;
    tokenBridge[token] = bridge;
    emit AddSupportedToken(token, oracle, bridge);
    // tokenFeeBps[token] is never set here
}
```

The only way to set a non-zero fee is a separate admin call to `setTokenFeeBps`. Until that call is made — or if it is never made — every call to `deposit(token, amount, referralId)` computes `fee = 0`, increments `feeEarnedInToken[token]` by zero, and transfers the full rsETH amount to the depositor.

By contrast, ETH deposits use `feeBps` which is set during `initialize`:

```solidity
feeBps = _feeBps;
```

This asymmetry means the protocol systematically under-collects fees on all ERC-20 token deposits in `RSETHPool`.

### Impact Explanation
Every token deposit on the Arbitrum `RSETHPool` pays zero protocol fee until an explicit admin action is taken. The protocol loses all fee revenue on token deposits — revenue that is the intended yield of operating the L2 swap pool. This matches **High — Theft of unclaimed yield**: the protocol's entitled fee income is silently forfeited to depositors who receive a better-than-intended exchange rate.

### Likelihood Explanation
The `addSupportedToken` call and the `setTokenFeeBps` call are two separate transactions with no on-chain enforcement linking them. Any gap between the two — whether due to operational oversight, a multi-step deployment sequence, or a deliberate omission — leaves the token fee at zero. Because the default is zero and no revert or warning signals the missing fee, this condition can persist indefinitely without detection. Any ordinary depositor benefits automatically; no special knowledge or privilege is required.

### Recommendation
Initialize `tokenFeeBps[token]` inside `addSupportedToken` by accepting a `_feeBps` parameter, or add a non-zero fee guard:

```solidity
function addSupportedToken(
    address token,
    address oracle,
    address bridge,
    uint256 _feeBps          // add this
) external onlyRole(TIMELOCK_ROLE) {
    ...
    require(_feeBps > 0, "fee must be set");
    tokenFeeBps[token] = _feeBps;
    ...
}
```

Alternatively, fall back to the global `feeBps` when `tokenFeeBps[token]` is zero, mirroring the ETH deposit path.

### Proof of Concept
1. Admin calls `addSupportedToken(wstETH, oracle, bridge)` — `tokenFeeBps[wstETH]` remains `0`.
2. Depositor calls `deposit(wstETH, 10 ether, "")`.
3. `viewSwapRsETHAmountAndFee(10 ether, wstETH)` computes `fee = 10 ether * 0 / 10_000 = 0`.
4. `feeEarnedInToken[wstETH] += 0` — no fee accrues.
5. Depositor receives rsETH equivalent to the full `10 ether` of wstETH with zero fee deducted.
6. Repeat indefinitely; protocol collects nothing on all wstETH deposits. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

### Citations

**File:** contracts/pools/RSETHPool.sol (L87-89)
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
