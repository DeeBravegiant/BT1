### Title
Missing Slippage Protection in L2 Pool `deposit()` Functions Allows Users to Receive Fewer wrsETH/rsETH Than Expected - (File: contracts/pools/RSETHPoolV3.sol)

---

### Summary

The L2 pool deposit functions across `RSETHPoolV3`, `RSETHPoolNoWrapper`, and `RSETHPoolV3WithNativeChainBridge` lack a `minRSETHAmountExpected` (slippage protection) parameter. Users who preview the swap via `viewSwapRsETHAmountAndFee()` and then submit a `deposit()` transaction have no on-chain guarantee that the rate used at execution matches the rate they observed. If the oracle rate changes between preview and execution — which happens routinely as staking rewards accrue and `updateRate()` is callable by anyone — the user receives fewer wrsETH/rsETH than expected with no recourse.

This is the direct structural analog to the Cooler M-4 finding: a user observes favorable terms, submits a transaction, and the terms change before execution with no parameter to enforce a minimum acceptable outcome.

---

### Finding Description

Every L2 pool deposit function computes the output amount at execution time by reading the live oracle rate:

```solidity
// RSETHPoolV3.sol
function deposit(string memory referralId) external payable nonReentrant whenNotPaused ... {
    uint256 amount = msg.value;
    (uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount);
    feeEarnedInETH += fee;
    wrsETH.mint(msg.sender, rsETHAmount);
}
``` [1](#0-0) 

The rate is fetched from the oracle at call time:

```solidity
function viewSwapRsETHAmountAndFee(uint256 amount) public view returns (uint256 rsETHAmount, uint256 fee) {
    fee = amount * feeBps / 10_000;
    uint256 amountAfterFee = amount - fee;
    uint256 rsETHToETHrate = getRate();
    rsETHAmount = amountAfterFee * 1e18 / rsETHToETHrate;
}
``` [2](#0-1) 

The same pattern exists in `RSETHPoolNoWrapper.deposit()`: [3](#0-2) 

And in `RSETHPoolV3WithNativeChainBridge.deposit()`: [4](#0-3) 

There is **no `minRSETHAmountExpected` parameter** in any of these functions. Compare this to `LRTDepositPool.depositETH()` on L1, which correctly enforces a minimum:

```solidity
function depositETH(uint256 minRSETHAmountExpected, string calldata referralId) external payable ...
``` [5](#0-4) 

The L1 version reverts if the minted amount falls below the user's minimum: [6](#0-5) 

The oracle rate on L2 is updated via `CrossChainRateProvider.updateRate()`, which has **no access control** — it is callable by any external account:

```solidity
function updateRate() external payable nonReentrant {
    uint256 latestRate = getLatestRate();
    ...
    ILayerZeroEndpoint(layerZeroEndpoint).send{value: msg.value}(...);
    emit RateUpdated(rate);
}
``` [7](#0-6) 

The `CrossChainRateReceiver.lzReceive()` stores the pushed rate, which is then returned by `getRate()` used in all pool deposit calculations: [8](#0-7) 

---

### Impact Explanation

A user who calls `viewSwapRsETHAmountAndFee()` to preview their swap and then submits `deposit()` may receive fewer wrsETH/rsETH than the preview indicated. Because the rsETH/ETH rate only increases over time (staking rewards accrue), a rate update between preview and execution always results in fewer output tokens. The user's ETH value is approximately preserved (each wrsETH is worth more ETH), but the contract fails to deliver the token quantity the user was shown and acted upon. This maps to **Low — Contract fails to deliver promised returns, but doesn't lose value.**

---

### Likelihood Explanation

The rsETH/ETH rate increases continuously as EigenLayer staking rewards accrue. Any pending `deposit()` transaction that sits in the mempool for more than a few seconds is exposed to a rate update. Additionally, `CrossChainRateProvider.updateRate()` is permissionlessly callable, meaning any actor can push a fresh (higher) rate to the L2 oracle at any time, including in the same block as a user's deposit. This is a routine operational condition, not a rare edge case.

---

### Recommendation

Add a `minRSETHAmountExpected` parameter to all L2 pool `deposit()` functions, mirroring the protection already present in `LRTDepositPool.depositETH()` and `LRTDepositPool.depositAsset()`. Revert if the computed output falls below the caller-specified minimum:

```solidity
function deposit(string memory referralId, uint256 minRSETHAmountExpected)
    external payable nonReentrant whenNotPaused ...
{
    ...
    (uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount);
    if (rsETHAmount < minRSETHAmountExpected) revert MinimumAmountToReceiveNotMet();
    ...
}
```

Apply the same fix to the token-deposit overload and to `RSETHPoolNoWrapper`, `RSETHPoolV3WithNativeChainBridge`, and `RSETHPoolV3ExternalBridge`.

---

### Proof of Concept

1. User calls `viewSwapRsETHAmountAndFee(1 ether)` on `RSETHPoolV3`. Oracle rate is `1.05e18` (1 rsETH = 1.05 ETH). Preview shows `~0.952 wrsETH`.
2. User submits `deposit{value: 1 ether}("ref")`.
3. Before the transaction is included, anyone calls `CrossChainRateProvider.updateRate()` on L1, which pushes the current rate `1.06e18` to the L2 `CrossChainRateReceiver` via LayerZero.
4. User's `deposit()` executes. `getRate()` now returns `1.06e18`. User receives `~0.943 wrsETH` — approximately 1% fewer tokens than the preview showed.
5. The user has no on-chain mechanism to reject this outcome. The `deposit()` function accepts any rate the oracle returns at execution time.

The attacker-controlled entry path is: `CrossChainRateProvider.updateRate()` (permissionless) → `CrossChainRateReceiver.lzReceive()` → updated `rate` storage → `RSETHPoolV3.deposit()` reads stale-vs-fresh rate discrepancy with no floor check. [7](#0-6) [9](#0-8) [10](#0-9)

### Citations

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

**File:** contracts/LRTDepositPool.sol (L665-669)
```text
        rsethAmountToMint = getRsETHAmountToMint(asset, depositAmount);

        if (rsethAmountToMint < minRSETHAmountExpected) {
            revert MinimumAmountToReceiveNotMet();
        }
```

**File:** contracts/cross-chain/CrossChainRateProvider.sol (L85-101)
```text
    function updateRate() external payable nonReentrant {
        uint256 latestRate = getLatestRate();

        bytes memory remoteAndLocalAddresses = abi.encodePacked(rateReceiver, address(this));

        rate = latestRate;

        lastUpdated = block.timestamp;

        bytes memory _payload = abi.encode(latestRate);

        ILayerZeroEndpoint(layerZeroEndpoint).send{ value: msg.value }(
            dstChainId, remoteAndLocalAddresses, _payload, payable(msg.sender), address(0x0), bytes("")
        );

        emit RateUpdated(rate);
    }
```

**File:** contracts/cross-chain/CrossChainRateReceiver.sol (L82-105)
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
    }

    /// @notice Gets the last stored rate in the contract
    function getRate() external view returns (uint256) {
        return rate;
    }
```
