### Title
Token Deposits Are Free of Any Fees by Default Due to Uninitialized `tokenFeeBps` - (File: contracts/pools/RSETHPool.sol)

### Summary
`RSETHPool.sol` (the Arbitrum L2 pool) maintains two separate fee variables: a global `feeBps` for ETH deposits and a per-token `tokenFeeBps[token]` mapping for ERC20 token deposits. The `tokenFeeBps` mapping is never initialized when a token is added via `addSupportedToken`, so it defaults to `0`. As a result, all token deposits (e.g., wstETH) are permanently fee-free unless the admin separately calls `setTokenFeeBps`, while ETH deposits correctly charge fees.

### Finding Description

`RSETHPool.sol` has two deposit paths:

**ETH deposit path** — uses the global `feeBps`: [1](#0-0) 

**Token deposit path** — uses `tokenFeeBps[token]`, which defaults to `0`: [2](#0-1) 

When `addSupportedToken` is called, it sets `supportedTokenOracle[token]` and `tokenBridge[token]`, but never sets `tokenFeeBps[token]`: [3](#0-2) 

The setter `setTokenFeeBps` exists but must be called separately and explicitly: [4](#0-3) 

Because `tokenFeeBps[token]` is a mapping that defaults to `0`, every token deposit computes `fee = amount * 0 / 10_000 = 0`. The token deposit function then records `feeEarnedInToken[token] += 0` and transfers the full token amount worth of wrsETH to the user with no fee deducted: [5](#0-4) 

### Impact Explanation

The protocol collects zero fees on all token deposits (e.g., wstETH on Arbitrum) while correctly collecting fees on ETH deposits. This is a direct loss of protocol fee revenue — the protocol fails to collect yield it is designed and expected to collect. Every token depositor receives the full rsETH amount without paying the intended fee, permanently leaking value from the protocol treasury.

**Impact: High. Theft of unclaimed yield.**

### Likelihood Explanation

The vulnerability is active from the moment any supported token is added without a subsequent `setTokenFeeBps` call. Any unprivileged user calling `deposit(address token, uint256 amount, string referralId)` with a supported token (e.g., wstETH) triggers the fee-free path. No special conditions are required — the default state of the mapping is the vulnerable state. The entry path is fully public and permissionless.

### Recommendation

Set `tokenFeeBps[token]` inside `addSupportedToken` (or require it as a parameter), so that newly added tokens inherit a non-zero fee from the moment they are supported. Alternatively, fall back to the global `feeBps` when `tokenFeeBps[token]` is zero, mirroring the ETH deposit behavior.

```solidity
function addSupportedToken(address token, address oracle, address bridge, uint256 _feeBps) external onlyRole(TIMELOCK_ROLE) {
    // ... existing checks ...
    tokenFeeBps[token] = _feeBps;
    // ...
}
```

### Proof of Concept

1. Admin calls `addSupportedToken(wstETH, oracle, bridge)` — `tokenFeeBps[wstETH]` remains `0`.
2. User calls `deposit(wstETH, 10 ether, "ref")`.
3. `viewSwapRsETHAmountAndFee(10 ether, wstETH)` computes `fee = 10 ether * 0 / 10_000 = 0`.
4. `feeEarnedInToken[wstETH] += 0` — no fee is recorded.
5. User receives wrsETH equivalent to the full `10 ether` of wstETH with zero fee deducted.
6. Compare: an ETH deposit of equivalent value via `deposit(referralId)` would compute `fee = 10 ether * feeBps / 10_000 > 0` and correctly deduct it.

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

**File:** contracts/pools/RSETHPool.sol (L311-320)
```text
    function viewSwapRsETHAmountAndFee(uint256 amount) public view returns (uint256 rsETHAmount, uint256 fee) {
        fee = amount * feeBps / 10_000;
        uint256 amountAfterFee = amount - fee;

        // rate of rsETH in ETH
        uint256 rsETHToETHrate = getRate();

        // Calculate the final rsETH amount
        rsETHAmount = amountAfterFee * 1e18 / rsETHToETHrate;
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
