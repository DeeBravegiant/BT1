### Title
`RSETHPool.sol` `tokenFeeBps` Never Initialized in `addSupportedToken`, Causing Zero Fees on Token Deposits - (File: contracts/pools/RSETHPool.sol)

### Summary
`RSETHPool.sol` declares a per-token fee mapping `tokenFeeBps` that is read in the token-deposit fee calculation, but `addSupportedToken` never sets it. Every newly added token permanently defaults to a 0 fee unless the admin separately calls `setTokenFeeBps`. This mirrors the ToyBox pattern: a mapping is consumed in a critical calculation but the initialization path that creates the entry never populates it.

### Finding Description
`RSETHPool.sol` stores per-token fee rates in `tokenFeeBps`:

```solidity
// line 88
mapping(address token => uint256 feeBps) public tokenFeeBps;
```

This mapping is the sole source of the fee charged on token deposits:

```solidity
// lines 335-336
uint256 feeBpsForToken = tokenFeeBps[token];
fee = amount * feeBpsForToken / 10_000;
``` [1](#0-0) 

The only way to add a supported token is `addSupportedToken`, which sets the oracle and bridge but **never touches `tokenFeeBps`**:

```solidity
// lines 637-656
function addSupportedToken(address token, address oracle, address bridge) external onlyRole(TIMELOCK_ROLE) {
    ...
    supportedTokenList.push(token);
    supportedTokenOracle[token] = oracle;
    tokenBridge[token] = bridge;
    // tokenFeeBps[token] is never set here
    emit AddSupportedToken(token, oracle, bridge);
}
``` [2](#0-1) 

A separate `setTokenFeeBps` function exists but is not called from `addSupportedToken` and accepts no default value parameter: [3](#0-2) 

Contrast this with ETH deposits, where `feeBps` is set at initialization time and always non-zero by design. Token deposits have no equivalent guarantee.

### Impact Explanation
Every token added via `addSupportedToken` starts with `tokenFeeBps[token] == 0`. All token deposits through `deposit(address token, uint256 amount, string referralId)` compute `fee = 0`, so the protocol collects zero fee revenue on those deposits. If the admin never calls `setTokenFeeBps`, this is permanent. Even if the admin does call it later, there is an open window during which users can deposit at zero cost.

**Impact: Low** — The contract fails to deliver its promised fee-collection behavior for token deposits, but no user funds are lost.

### Likelihood Explanation
**Likelihood: High** — Every token addition via `addSupportedToken` reproduces the condition automatically. No special attacker action is needed; any ordinary depositor calling `deposit(token, amount, referralId)` during the window (or indefinitely if the admin never calls `setTokenFeeBps`) triggers the zero-fee path. [4](#0-3) 

### Recommendation
Add a `feeBps` parameter to `addSupportedToken` and set `tokenFeeBps[token]` inside it, analogous to how `feeBps` is set during `initialize`:

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

### Proof of Concept
1. Admin calls `addSupportedToken(wstETH, oracle, bridge)` — `tokenFeeBps[wstETH]` is `0`.
2. Any user calls `deposit(wstETH, 1e18, "ref")`.
3. `viewSwapRsETHAmountAndFee(1e18, wstETH)` executes: `feeBpsForToken = tokenFeeBps[wstETH] = 0`, so `fee = 0`.
4. User receives the full rsETH amount with no fee deducted; `feeEarnedInToken[wstETH]` remains `0`.
5. Protocol collects no fee revenue on the deposit, contrary to its design intent. [5](#0-4) [6](#0-5)

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
