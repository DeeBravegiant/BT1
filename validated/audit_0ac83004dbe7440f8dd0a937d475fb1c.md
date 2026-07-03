### Title
Missing Minimum rsETH Output Protection in L2 Pool Deposit Functions - (File: contracts/pools/RSETHPoolV3ExternalBridge.sol, RSETHPool.sol, RSETHPoolNoWrapper.sol, RSETHPoolV2ExternalBridge.sol, RSETHPoolV3WithNativeChainBridge.sol)

### Summary
All L2 pool `deposit` functions lack a `minRsETHAmount` parameter, exposing depositors to receiving fewer rsETH tokens than expected when the oracle rate changes between transaction submission and on-chain execution. The L1 `LRTDepositPool` already enforces this protection via `minRSETHAmountExpected`, but the L2 pool contracts do not.

### Finding Description
Every L2 pool `deposit` function computes the rsETH output at execution time by querying the oracle:

```solidity
// RSETHPoolV3ExternalBridge.sol – viewSwapRsETHAmountAndFee (ETH path)
rsETHAmount = amountAfterFee * 1e18 / rsETHToETHrate;   // line 426

// RSETHPoolV3ExternalBridge.sol – viewSwapRsETHAmountAndFee (token path)
rsETHAmount = amountAfterFee * tokenToETHRate / rsETHToETHrate;  // line 452
```

The `deposit` entry-points pass the computed amount directly to the user with no floor check:

```solidity
// RSETHPoolV3ExternalBridge.sol deposit() – ETH path (lines 366-384)
(uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount);
feeEarnedInETH += fee;
wrsETH.mint(msg.sender, rsETHAmount);   // no minRsETHAmount guard
```

The same pattern is present in every L2 pool variant:
- `RSETHPool.sol` lines 265–278 and 284–305
- `RSETHPoolNoWrapper.sol` lines 231–244 and 250–271
- `RSETHPoolV2ExternalBridge.sol` lines 289–301
- `RSETHPoolV3WithNativeChainBridge.sol` lines 282–301 and 307–329

By contrast, the L1 `LRTDepositPool` already enforces a minimum:

```solidity
// LRTDepositPool.sol _beforeDeposit (lines 667-669)
if (rsethAmountToMint < minRSETHAmountExpected) {
    revert MinimumAmountToReceiveNotMet();
}
```

### Impact Explanation
A user submits a deposit transaction expecting a specific rsETH amount based on the current oracle rate. If the oracle is updated (rsETH/ETH rate increases) before the transaction is mined, the user receives fewer rsETH tokens than expected. The ETH is fully consumed; the shortfall in rsETH is a direct, unrecoverable loss of value to the depositor. This matches the "contract fails to deliver promised returns" impact class.

**Impact: Low** — The user does not lose their principal asset class (ETH is exchanged for rsETH at a worse rate, not stolen), but receives fewer rsETH tokens than the rate they observed when constructing the transaction.

### Likelihood Explanation
The rsETH/ETH oracle rate is updated periodically as staking rewards accrue. Rate updates are not triggered by other users' transactions, so classic sandwich attacks are not directly applicable. However, any oracle update that lands in the same block or a block between user submission and inclusion causes silent slippage with no recourse. On active L2s with frequent oracle refreshes this is a realistic, low-probability event.

**Likelihood: Low**

### Recommendation
Add a `minRsETHAmount` parameter to all L2 pool `deposit` entry-points and revert when the computed `rsETHAmount` falls below it, mirroring the existing protection in `LRTDepositPool._beforeDeposit`:

```solidity
function deposit(string memory referralId, uint256 minRsETHAmount)
    external payable nonReentrant whenNotPaused ...
{
    ...
    (uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount);
    if (rsETHAmount < minRsETHAmount) revert MinimumAmountToReceiveNotMet();
    ...
}
```

### Proof of Concept
1. Oracle reports `rsETHToETHrate = 1.05e18` (1 rsETH = 1.05 ETH).
2. User submits `deposit{value: 1 ETH}` expecting ≈ `0.952 rsETH`.
3. Before the tx is mined, the oracle is updated to `rsETHToETHrate = 1.10e18`.
4. `viewSwapRsETHAmountAndFee(1e18)` now returns ≈ `0.909 rsETH`.
5. `wrsETH.mint(msg.sender, 0.909e18)` executes — user receives ~4.5% fewer rsETH than expected with no revert and no recourse. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5)

### Citations

**File:** contracts/pools/RSETHPoolV3ExternalBridge.sol (L366-384)
```text
    function deposit(string memory referralId)
        external
        payable
        nonReentrant
        whenNotPaused
        limitDailyMint(msg.value, ETH_IDENTIFIER)
    {
        uint256 amount = msg.value;

        if (amount == 0) revert InvalidAmount();

        (uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount);

        feeEarnedInETH += fee;

        wrsETH.mint(msg.sender, rsETHAmount);

        emit SwapOccurred(msg.sender, rsETHAmount, fee, referralId);
    }
```

**File:** contracts/pools/RSETHPoolV3ExternalBridge.sol (L418-427)
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

**File:** contracts/pools/RSETHPoolV3ExternalBridge.sol (L440-453)
```text
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

**File:** contracts/pools/RSETHPool.sol (L265-278)
```text
    function deposit(string memory referralId) external payable nonReentrant whenNotPaused {
        if (!isEthDepositEnabled) revert EthDepositDisabled();
        uint256 amount = msg.value;

        if (amount == 0) revert InvalidAmount();

        (uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount);

        feeEarnedInETH += fee;

        IERC20(address(wrsETH)).safeTransfer(msg.sender, rsETHAmount);

        emit SwapOccurred(msg.sender, rsETHAmount, fee, referralId);
    }
```

**File:** contracts/pools/RSETHPoolNoWrapper.sol (L231-244)
```text
    function deposit(string memory referralId) external payable nonReentrant whenNotPaused {
        if (!isEthDepositEnabled) revert EthDepositDisabled();
        uint256 amount = msg.value;

        if (amount == 0) revert InvalidAmount();

        (uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount);

        feeEarnedInETH += fee;

        rsETH.safeTransfer(msg.sender, rsETHAmount);

        emit SwapOccurred(msg.sender, rsETHAmount, fee, referralId);
    }
```

**File:** contracts/LRTDepositPool.sol (L665-669)
```text
        rsethAmountToMint = getRsETHAmountToMint(asset, depositAmount);

        if (rsethAmountToMint < minRSETHAmountExpected) {
            revert MinimumAmountToReceiveNotMet();
        }
```
