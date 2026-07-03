### Title
No Slippage Protection in L2 Pool `deposit()` Functions Allows Users to Receive Fewer wrsETH Than Expected - (File: contracts/pools/RSETHPoolV3.sol, contracts/pools/RSETHPoolV3ExternalBridge.sol)

### Summary
All L2 liquidity pool `deposit()` functions across `RSETHPoolV3`, `RSETHPoolV3ExternalBridge`, `RSETHPoolV3WithNativeChainBridge`, `RSETHPool`, and `RSETHPoolNoWrapper` compute the wrsETH/rsETH amount to mint using a live oracle rate (`getRate()`) with no `minRsETHOut` guard. If the cross-chain rate is updated between a user's preview call and their deposit transaction execution, the user receives fewer tokens than expected with no recourse.

### Finding Description

Every L2 deposit function follows this pattern (shown for `RSETHPoolV3`):

```solidity
// RSETHPoolV3.sol – deposit(string referralId)
function deposit(string memory referralId) external payable ... {
    uint256 amount = msg.value;
    (uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount);
    feeEarnedInETH += fee;
    wrsETH.mint(msg.sender, rsETHAmount);   // no minRsETHOut check
}

function viewSwapRsETHAmountAndFee(uint256 amount) public view returns (...) {
    fee = amount * feeBps / 10_000;
    uint256 amountAfterFee = amount - fee;
    uint256 rsETHToETHrate = getRate();          // live oracle read
    rsETHAmount = amountAfterFee * 1e18 / rsETHToETHrate;
}
```

The oracle (`rsETHOracle`) is a `CrossChainRateReceiver` whose `rate` is updated via LayerZero messages pushed from L1 by `MultiChainRateProvider`. The update is a normal, permissionless-to-trigger protocol operation. Because rsETH continuously accrues restaking yield, the rate monotonically increases over time. Any rate update that lands between a user's off-chain preview and their on-chain execution silently reduces the wrsETH minted.

The same pattern is present in every deposit variant across all pool contracts:
- `RSETHPoolV3.deposit(string)` and `RSETHPoolV3.deposit(address,uint256,string)`
- `RSETHPoolV3ExternalBridge.deposit(string)` and `RSETHPoolV3ExternalBridge.deposit(address,uint256,string)`
- `RSETHPoolV3WithNativeChainBridge.deposit(...)` variants
- `RSETHPool.deposit(...)` and `RSETHPoolNoWrapper.deposit(...)` variants

By contrast, the L1 `LRTDepositPool` correctly accepts a `minRSETHAmountExpected` parameter and enforces it via `_beforeDeposit`, demonstrating the protocol is aware of this protection pattern but did not apply it to L2 pools.

### Impact Explanation

**Impact: Low** — Contract fails to deliver the promised return without losing the deposited principal. A user who previews `viewSwapRsETHAmountAndFee` and then submits a deposit may receive materially fewer wrsETH tokens than the preview indicated, with no ability to revert. The deposited ETH/LST is retained by the pool and correctly accounted for, so no funds are stolen, but the user's share of the restaking position is silently diluted relative to their expectation.

### Likelihood Explanation

**Likelihood: Medium** — The rsETH/ETH rate is updated via cross-chain LayerZero messages on a regular cadence. On active L2 chains (Arbitrum, Base, Optimism), rate updates can arrive at any time. Any user whose transaction is pending in the mempool when a rate update is delivered will be affected. This is a routine, non-adversarial condition requiring no attacker.

### Recommendation

Add a `minRsETHOut` parameter to all L2 `deposit()` functions and revert if the computed `rsETHAmount` falls below it, mirroring the `minRSETHAmountExpected` guard already present in `LRTDepositPool.depositETH()` and `LRTDepositPool.depositAsset()` on L1.

```solidity
function deposit(string memory referralId, uint256 minRsETHOut)
    external payable nonReentrant whenNotPaused ...
{
    uint256 amount = msg.value;
    if (amount == 0) revert InvalidAmount();
    (uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount);
    if (rsETHAmount < minRsETHOut) revert InsufficientOutputAmount();
    feeEarnedInETH += fee;
    wrsETH.mint(msg.sender, rsETHAmount);
    emit SwapOccurred(msg.sender, rsETHAmount, fee, referralId);
}
```

### Proof of Concept

1. The rsETH/ETH rate on L2 is `1.05e18` (rsETH is worth 1.05 ETH).
2. Alice calls `viewSwapRsETHAmountAndFee(1 ether)` and sees she will receive `~0.952 wrsETH` (ignoring fees).
3. Alice submits `deposit{value: 1 ether}("ref")`.
4. Before Alice's transaction is mined, a LayerZero rate update arrives and sets the rate to `1.10e18`.
5. Alice's transaction executes: `rsETHAmount = 1e18 * 1e18 / 1.10e18 = ~0.909 wrsETH`.
6. Alice receives `~0.909 wrsETH` instead of the `~0.952 wrsETH` she previewed — a ~4.5% shortfall — with no revert and no recourse.

**Root cause chain:**
- `CrossChainRateReceiver.lzReceive()` updates `rate` at any time [1](#0-0) 
- `RSETHPoolV3.getRate()` reads this live value [2](#0-1) 
- `viewSwapRsETHAmountAndFee` divides by the live rate with no floor [3](#0-2) 
- `deposit()` mints without any minimum output check [4](#0-3) 
- Same pattern in `RSETHPoolV3ExternalBridge.deposit()` [5](#0-4) 
- L1 correctly uses `minRSETHAmountExpected` as a guard [6](#0-5)

### Citations

**File:** contracts/cross-chain/CrossChainRateReceiver.sol (L82-99)
```text
    function lzReceive(uint16 _srcChainId, bytes memory _srcAddress, uint64, bytes calldata _payload) external {
        require(msg.sender == layerZeroEndpoint, "Sender should be lz endpoint");

        address srcAddress;
        assembly {
            srcAddress := mload(add(_srcAddress, 20))
        }

        require(_srcChainId == srcChainId, "Src chainId must be correct");
        require(srcAddress == rateProvider, "Src address must be provider");

        uint256 _rate = abi.decode(_payload, (uint256));

        rate = _rate;

        lastUpdated = block.timestamp;

        emit RateUpdated(_rate);
```

**File:** contracts/pools/RSETHPoolV3.sol (L234-237)
```text
    /// @dev Gets the rate from the rsETHOracle
    function getRate() public view returns (uint256) {
        return IOracle(rsETHOracle).getRate();
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

**File:** contracts/pools/RSETHPoolV3.sol (L299-308)
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

**File:** contracts/LRTDepositPool.sol (L76-93)
```text
    function depositETH(
        uint256 minRSETHAmountExpected,
        string calldata referralId
    )
        external
        payable
        nonReentrant
        whenNotPaused
        onlySupportedAsset(LRTConstants.ETH_TOKEN)
    {
        // checks
        uint256 rsethAmountToMint = _beforeDeposit(LRTConstants.ETH_TOKEN, msg.value, minRSETHAmountExpected);

        // interactions
        _mintRsETH(rsethAmountToMint);

        emit ETHDeposit(msg.sender, msg.value, rsethAmountToMint, referralId);
    }
```
