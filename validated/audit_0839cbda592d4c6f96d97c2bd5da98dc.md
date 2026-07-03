### Title
Implicit 18-Decimal Assumption in `viewSwapRsETHAmountAndFee` Causes Severe rsETH Under-Minting for Non-18-Decimal Tokens - (File: contracts/pools/RSETHPool.sol)

---

### Summary

The `viewSwapRsETHAmountAndFee(uint256 amount, address token)` function in `RSETHPool.sol` and `RSETHPoolV3.sol` uses a formula that implicitly assumes all supported tokens have 18 decimals. If a token with fewer decimals (e.g., USDC with 6) is added via `addSupportedToken`, users depositing that token will receive `10^(18 − tokenDecimals)` times less rsETH than they should, effectively losing their deposited funds. This is the direct analog of pyUmbral's hardcoded `197` bytes that assumed a fixed key size: a hardcoded implicit precision assumption that breaks silently for any configuration that deviates from the default.

---

### Finding Description

In `RSETHPool.sol` the token-deposit swap calculation is:

```solidity
// rate of rsETH in ETH
uint256 rsETHToETHrate = getRate();

// rate of token in ETH
uint256 tokenToETHRate = IOracle(supportedTokenOracle[token]).getRate();

// Calculate the final rsETH amount
rsETHAmount = amountAfterFee * tokenToETHRate / rsETHToETHrate;
``` [1](#0-0) 

The formula `amountAfterFee * tokenToETHRate / rsETHToETHrate` is dimensionally correct **only** when `amountAfterFee` is expressed in 1e18 units (i.e., the token has 18 decimals). Both `tokenToETHRate` and `rsETHToETHrate` are in 1e18 precision (price of 1 whole token in ETH). For an 18-decimal token:

```
1e18 (token units) * 1.15e18 (ETH/token) / 1.05e18 (ETH/rsETH) ≈ 1.095e18 rsETH units  ✓
```

For a 6-decimal token (USDC at 0.0005 ETH/USDC):

```
1e6 (token units) * 5e14 (ETH/USDC) / 1.05e18 (ETH/rsETH) ≈ 476 rsETH wei  ✗
```

The correct result is `≈ 4.76e14` rsETH wei — the formula produces a value **1e12 times too small**.

The `addSupportedToken` function that registers new tokens performs no check on token decimals:

```solidity
function addSupportedToken(address token, address oracle, address bridge) external onlyRole(TIMELOCK_ROLE) {
    UtilLib.checkNonZeroAddress(token);
    UtilLib.checkNonZeroAddress(oracle);
    UtilLib.checkNonZeroAddress(bridge);
    ...
    supportedTokenList.push(token);
    supportedTokenOracle[token] = oracle;
    tokenBridge[token] = bridge;
``` [2](#0-1) 

The identical pattern exists in `RSETHPoolV3.sol`: [3](#0-2) 

---

### Impact Explanation

A user depositing 1,000 USDC (6 decimals, ~$1,000) would receive approximately `4.76e-13` rsETH instead of `~0.476` rsETH. Their 1,000 USDC is transferred to the pool and subsequently bridged to L1 and absorbed into the protocol, while they hold a negligible rsETH balance that cannot be redeemed for any meaningful value. This constitutes **permanent loss of user funds** — mapping to **Critical: Direct theft of any user funds**.

---

### Likelihood Explanation

The `addSupportedToken` function is callable by any `TIMELOCK_ROLE` holder. A protocol operator legitimately expanding collateral support to include USDC, USDT, or any other sub-18-decimal token would trigger this bug without any indication of error. No malicious intent is required — the function accepts the token, the oracle, and the bridge without any decimal validation. This mirrors the pyUmbral scenario exactly: a legitimate implementer using a non-default curve triggers silent, catastrophic mis-accounting.

---

### Recommendation

1. **Normalize for token decimals** in `viewSwapRsETHAmountAndFee`:
   ```solidity
   uint8 tokenDecimals = IERC20Metadata(token).decimals();
   rsETHAmount = amountAfterFee * tokenToETHRate * (10 ** (18 - tokenDecimals)) / rsETHToETHrate;
   ```
2. **Alternatively**, enforce 18-decimal tokens only in `addSupportedToken`:
   ```solidity
   require(IERC20Metadata(token).decimals() == 18, "Only 18-decimal tokens supported");
   ```
3. **Define the oracle rate convention** (price per whole token vs. price per token unit) in a single canonical location — analogous to the pyUmbral recommendation to define the curve in one canonical place.

---

### Proof of Concept

1. Admin calls `addSupportedToken(USDC_ADDRESS, usdcOracle, bridge)` — USDC has 6 decimals; no revert occurs.
2. User calls `deposit(USDC_ADDRESS, 1_000e6, "ref")` depositing 1,000 USDC.
3. `viewSwapRsETHAmountAndFee(1_000e6, USDC_ADDRESS)` executes:
   - `tokenToETHRate = 5e14` (0.0005 ETH per 1 USDC, 1e18 precision)
   - `rsETHToETHrate = 1.05e18`
   - `rsETHAmount = 1_000e6 * 5e14 / 1.05e18 ≈ 476_190 wei ≈ 4.76e-13 rsETH`
4. `wrsETH.mint(msg.sender, 476_190)` — user receives dust.
5. `IERC20(USDC).safeTransferFrom(user, pool, 1_000e6)` — 1,000 USDC is absorbed into the pool and bridged to L1.
6. User's ~$1,000 of USDC is permanently lost; they hold `4.76e-13` rsETH with negligible redemption value. [4](#0-3) [5](#0-4) [6](#0-5)

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

**File:** contracts/pools/RSETHPool.sol (L637-655)
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
```

**File:** contracts/pools/RSETHPoolV3.sol (L271-293)
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
        limitDailyMint(amount, token)
    {
        if (amount == 0) revert InvalidAmount();

        IERC20(token).safeTransferFrom(msg.sender, address(this), amount);

        (uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount, token);

        feeEarnedInToken[token] += fee;

        wrsETH.mint(msg.sender, rsETHAmount);

        emit SwapOccurred(msg.sender, rsETHAmount, fee, referralId, token);
    }
```

**File:** contracts/pools/RSETHPoolV3.sol (L541-554)
```text
    function addSupportedToken(address token, address oracle) external onlyRole(TIMELOCK_ROLE) {
        UtilLib.checkNonZeroAddress(token);
        UtilLib.checkNonZeroAddress(oracle);

        if (supportedTokenOracle[token] != address(0)) {
            revert AlreadySupportedToken();
        }
        if (IOracle(oracle).getRate() == 0) {
            revert UnsupportedOracle();
        }
        supportedTokenList.push(token);
        supportedTokenOracle[token] = oracle;

        emit AddSupportedToken(token);
```
