### Title
Missing Minimum Output Check in L2 Pool Deposit Functions Exposes Users to Unbounded Slippage - (File: contracts/pools/RSETHPool.sol)

### Summary
All L2 pool `deposit()` functions lack a user-specified minimum output amount parameter. Unlike the L1 `LRTDepositPool.depositETH()` which enforces a `minRSETHAmountExpected` guard, the L2 pool variants accept ETH or LSTs and mint/transfer wrsETH/rsETH with no slippage floor. If the oracle rate changes between transaction submission and on-chain execution, users silently receive fewer tokens than they observed off-chain, and the transaction succeeds unconditionally.

### Finding Description
The L1 deposit path enforces a minimum output check:

```solidity
// LRTDepositPool._beforeDeposit()
rsethAmountToMint = getRsETHAmountToMint(asset, depositAmount);
if (rsethAmountToMint < minRSETHAmountExpected) {
    revert MinimumAmountToReceiveNotMet();
}
```

Every L2 pool deposit function omits this guard entirely. The output is computed from the live oracle rate at execution time and transferred/minted with no floor:

```solidity
// RSETHPool.deposit() — identical pattern in all five pool variants
(uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount);
feeEarnedInETH += fee;
IERC20(address(wrsETH)).safeTransfer(msg.sender, rsETHAmount);
```

The rate used is:

```solidity
rsETHAmount = amountAfterFee * 1e18 / rsETHToETHrate;   // rsETHToETHrate = IOracle(rsETHOracle).getRate()
```

If `rsETHToETHrate` rises between the moment the user previews the swap (via `viewSwapRsETHAmountAndFee`) and the moment the transaction is mined, the user receives fewer tokens than expected. No revert occurs; the contract considers the swap successful.

The same pattern is present in all five pool contracts:
- `RSETHPool.deposit(string)` and `RSETHPool.deposit(address,uint256,string)`
- `RSETHPoolNoWrapper.deposit(string)` and `RSETHPoolNoWrapper.deposit(address,uint256,string)`
- `RSETHPoolV3.deposit(string)` and `RSETHPoolV3.deposit(address,uint256,string)`
- `RSETHPoolV3ExternalBridge.deposit(string)` and `RSETHPoolV3ExternalBridge.deposit(address,uint256,string)`
- `RSETHPoolV3WithNativeChainBridge.deposit(string)` and `RSETHPoolV3WithNativeChainBridge.deposit(address,uint256,string)`

### Impact Explanation
Users receive fewer wrsETH/rsETH tokens than they observed when they constructed the transaction. The shortfall is proportional to the oracle rate movement. The user's ETH/LST input is fully consumed; no funds are returned. This matches the allowed impact class: **"Contract fails to deliver promised returns, but doesn't lose value" (Low)**.

### Likelihood Explanation
The rsETH oracle rate (`LRTOracle.rsETHPrice()`) is updated periodically as EigenLayer TVL accrues rewards or as LST prices move. Any oracle update that increases the rsETH/ETH rate between a user's off-chain preview and on-chain execution silently reduces the user's output. This is a routine, recurring condition rather than a rare edge case. Users relying on `viewSwapRsETHAmountAndFee` for pre-trade quotes are systematically exposed.

### Recommendation
Add a `minRsETHAmountExpected` parameter to every L2 pool `deposit()` function and revert if the computed output falls below it, mirroring the L1 pattern:

```solidity
function deposit(string memory referralId, uint256 minRsETHAmountExpected)
    external payable nonReentrant whenNotPaused
{
    ...
    (uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount);
    if (rsETHAmount < minRsETHAmountExpected) revert SlippageExceeded();
    ...
}
```

### Proof of Concept
1. User calls `viewSwapRsETHAmountAndFee(1 ether)` off-chain; oracle rate is `1.05e18` → expected output ≈ `0.952 wrsETH`.
2. Before the transaction is mined, the oracle is updated to `1.10e18`.
3. `RSETHPool.deposit{value: 1 ether}("")` executes; `viewSwapRsETHAmountAndFee` now returns ≈ `0.909 wrsETH`.
4. `safeTransfer(msg.sender, 0.909e18)` succeeds; no revert.
5. User receives ~4.5% fewer tokens than previewed, with no recourse.

The same sequence applies to token deposits and to all five pool variants. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5) [7](#0-6)

### Citations

**File:** contracts/LRTDepositPool.sol (L648-670)
```text
    function _beforeDeposit(
        address asset,
        uint256 depositAmount,
        uint256 minRSETHAmountExpected
    )
        private
        view
        returns (uint256 rsethAmountToMint)
    {
        if (depositAmount == 0 || depositAmount < minAmountToDeposit) {
            revert InvalidAmountToDeposit();
        }

        if (_checkIfDepositAmountExceedesCurrentLimit(asset, depositAmount)) {
            revert MaximumDepositLimitReached();
        }

        rsethAmountToMint = getRsETHAmountToMint(asset, depositAmount);

        if (rsethAmountToMint < minRSETHAmountExpected) {
            revert MinimumAmountToReceiveNotMet();
        }
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

**File:** contracts/pools/RSETHPoolV3.sol (L246-265)
```text
    function deposit(string memory referralId)
        external
        payable
        nonReentrant
        whenNotPaused
        limitDailyMint(msg.value, ETH_IDENTIFIER)
    {
        if (!isEthDepositEnabled) revert EthDepositDisabled();
        uint256 amount = msg.value;

        if (amount == 0) revert InvalidAmount();

        (uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount);

        feeEarnedInETH += fee;

        wrsETH.mint(msg.sender, rsETHAmount);

        emit SwapOccurred(msg.sender, rsETHAmount, fee, referralId);
    }
```

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

**File:** contracts/pools/RSETHPoolV3WithNativeChainBridge.sol (L282-301)
```text
    function deposit(string memory referralId)
        external
        payable
        nonReentrant
        whenNotPaused
        limitDailyMint(msg.value, ETH_IDENTIFIER)
    {
        if (!isEthDepositEnabled) revert EthDepositDisabled();
        uint256 amount = msg.value;

        if (amount == 0) revert InvalidAmount();

        (uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount);

        feeEarnedInETH += fee;

        wrsETH.mint(msg.sender, rsETHAmount);

        emit SwapOccurred(msg.sender, rsETHAmount, fee, referralId);
    }
```
