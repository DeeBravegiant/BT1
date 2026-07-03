### Title
Oracle-Based Exchange Rate in Multi-Token L2 Pool Deposits Enables Unprivileged Arbitrage - (File: contracts/pools/RSETHPoolV3.sol)

### Summary
The L2 pool contracts (`RSETHPoolV3`, `RSETHPoolV3ExternalBridge`, `RSETHPoolV3WithNativeChainBridge`) compute the rsETH amount minted for a token deposit by dividing two independent oracle rates. Because these two rates are sourced from separate oracles with independent update schedules, any unprivileged depositor can exploit a transient divergence between them to receive more rsETH than the fair-value equivalent of their deposit, extracting yield from existing rsETH holders.

### Finding Description
In `RSETHPoolV3.viewSwapRsETHAmountAndFee(uint256 amount, address token)`, the rsETH amount minted for a token deposit is computed as:

```solidity
// rate of rsETH in ETH
uint256 rsETHToETHrate = getRate();                                    // from rsETHOracle

// rate of token in ETH
uint256 tokenToETHRate = IOracle(supportedTokenOracle[token]).getRate(); // from per-token oracle

// Calculate the final rsETH amount
rsETHAmount = amountAfterFee * tokenToETHRate / rsETHToETHrate;
```

`rsETHToETHrate` is fetched from the pool's `rsETHOracle` (e.g., `InterimRSETHOracle`, whose rate is set manually by a MANAGER, or a cross-chain rate receiver updated via LayerZero). `tokenToETHRate` is fetched from a separate per-token oracle (e.g., a Chainlink feed or another rate provider). These two oracles operate on entirely independent update schedules and latencies.

When the two rates diverge — for example, when `tokenToETHRate` is stale-high (the token oracle has not yet reflected a price drop) or `rsETHToETHrate` is stale-low (the rsETH oracle has not yet been updated after rewards accrued) — the ratio `tokenToETHRate / rsETHToETHrate` exceeds the true fair-value ratio. Any unprivileged caller can call `deposit(token, amount, referralId)` at this moment and receive more wrsETH than the deposited token is actually worth in rsETH terms.

The same pattern is replicated identically across:
- `RSETHPoolV3ExternalBridge.viewSwapRsETHAmountAndFee` (lines 442–452)
- `RSETHPoolV3WithNativeChainBridge.viewSwapRsETHAmountAndFee` (lines 360–371)
- `RSETHPoolNoWrapper.viewSwapRsETHAmountAndFee` (lines 301–312)

There is no slippage guard, no TWAP, and no minimum-output check on the depositor side. The `deposit` function is entirely permissionless.

### Impact Explanation
Each over-minted wrsETH unit represents a claim on the protocol's ETH that was not backed by a corresponding deposit. The excess rsETH dilutes the share of every existing rsETH holder. Because the pool holds real ETH/LST assets that are eventually bridged to L1 and restaked, the dilution is a direct, permanent transfer of yield (and principal backing) from existing holders to the arbitrageur. This constitutes **theft of unclaimed yield** (High impact).

### Likelihood Explanation
Oracle divergence between a per-token Chainlink feed and the rsETH rate oracle is a routine, recurring condition:
- The rsETH rate oracle on L2 is updated via a cross-chain message (LayerZero) or manually by a MANAGER; it can lag by hours.
- Chainlink token feeds update on a heartbeat (e.g., 24 h) or a deviation threshold (e.g., 0.5%).
- Any period where the token appreciates relative to ETH but the rsETH oracle has not yet been refreshed creates an exploitable window.
- No special access, no flash loan, and no governance action is required — a standard EOA can execute the deposit in a single transaction.

Likelihood: **Medium**.

### Recommendation
1. Replace the two-oracle ratio with a single, unified price path: price the deposited token directly in rsETH terms (e.g., via a Chainlink rsETH/token feed or a TWAP from an on-chain AMM pool), eliminating the compounding staleness of two independent oracles.
2. Add a caller-supplied `minRsETHOut` parameter to `deposit` so users can protect themselves, and consider adding a protocol-level circuit breaker that reverts if the implied exchange rate deviates beyond a configurable band from the last known good rate.
3. As the external report recommends, consider moving toward an AMM-based or CDP-based accounting model for multi-asset pools rather than relying on spot oracle prices for exchange-rate determination.

### Proof of Concept
Assume:
- `rsETHToETHrate` = 1.05e18 (rsETH oracle, last updated 6 hours ago)
- `tokenToETHRate` for wstETH = 1.20e18 (Chainlink, current)
- True wstETH/rsETH fair rate = 1.20/1.06 ≈ 1.132 (rsETH oracle is stale by ~1%)

Attacker deposits 100 wstETH:
```
fee = 100e18 * feeBps / 10_000  (e.g., 0 if feeBps=0)
amountAfterFee = 100e18
rsETHAmount = 100e18 * 1.20e18 / 1.05e18 = 114.28e18 wrsETH
```
Fair value would be:
```
rsETHAmount_fair = 100e18 * 1.20e18 / 1.06e18 = 113.20e18 wrsETH
```
The attacker receives **~1.08 extra wrsETH** per 100 wstETH deposited. At scale (e.g., 10,000 wstETH deposited across multiple blocks during the staleness window), this represents ~108 rsETH of yield stolen from existing holders, redeemable at the current rsETH/ETH rate on any secondary market. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5)

### Citations

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

**File:** contracts/pools/RSETHPoolV3.sol (L315-335)
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
        fee = amount * feeBps / 10_000;
        uint256 amountAfterFee = amount - fee;

        // rate of rsETH in ETH
        uint256 rsETHToETHrate = getRate();

        // rate of token in ETH
        uint256 tokenToETHRate = IOracle(supportedTokenOracle[token]).getRate();

        // Calculate the final rsETH amount
        rsETHAmount = amountAfterFee * tokenToETHRate / rsETHToETHrate;
    }
```

**File:** contracts/pools/RSETHPoolV3ExternalBridge.sol (L433-452)
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
        fee = amount * feeBps / 10_000;
        uint256 amountAfterFee = amount - fee;

        // rate of rsETH in ETH
        uint256 rsETHToETHrate = getRate();

        // rate of token in ETH
        uint256 tokenToETHRate = IOracle(supportedTokenOracle[token]).getRate();

        // Calculate the final rsETH amount
        rsETHAmount = amountAfterFee * tokenToETHRate / rsETHToETHrate;
```

**File:** contracts/pools/RSETHPoolV3WithNativeChainBridge.sol (L351-371)
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
        fee = amount * feeBps / 10_000;
        uint256 amountAfterFee = amount - fee;

        // rate of rsETH in ETH
        uint256 rsETHToETHrate = getRate();

        // rate of token in ETH
        uint256 tokenToETHRate = IOracle(supportedTokenOracle[token]).getRate();

        // Calculate the final rsETH amount
        rsETHAmount = amountAfterFee * tokenToETHRate / rsETHToETHrate;
    }
```

**File:** contracts/pools/RSETHPoolNoWrapper.sol (L292-312)
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
        fee = amount * feeBps / 10_000;
        uint256 amountAfterFee = amount - fee;

        // rate of rsETH in ETH
        uint256 rsETHToETHrate = getRate();

        // rate of token in ETH
        uint256 tokenToETHRate = IOracle(supportedTokenOracle[token]).getRate();

        // Calculate the final rsETH amount
        rsETHAmount = amountAfterFee * tokenToETHRate / rsETHToETHrate;
    }
```

**File:** contracts/pools/oracle/InterimRSETHOracle.sol (L36-44)
```text
    function setRate(uint256 newRate) external onlyRole(MANAGER_ROLE) {
        _setRate(newRate);
    }

    /// @dev Internal function to set the rsETH/ETH rate
    function _setRate(uint256 newRate) internal {
        if (newRate < 1e18) revert InvalidRate();
        rate = newRate;
        emit RateUpdated(newRate);
```
