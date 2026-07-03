### Title
Division by Zero in `viewSwapRsETHAmountAndFee` Due to Uninitialized `CrossChainRateReceiver.rate` — (File: `contracts/cross-chain/CrossChainRateReceiver.sol`)

---

### Summary

`CrossChainRateReceiver.rate` defaults to `0` at deployment and is only updated when a LayerZero message arrives. Every L2 pool contract divides by this rate in `viewSwapRsETHAmountAndFee` without a zero-guard, mirroring the external report's pattern of uninitialized storage being used as a divisor. Any user calling `deposit()` before the first cross-chain rate message is delivered will receive a division-by-zero revert, temporarily freezing the deposit path.

---

### Finding Description

`CrossChainRateReceiver` stores the rsETH/ETH rate in a plain `uint256 public rate` field: [1](#0-0) 

This field is never set in the constructor of `CrossChainRateReceiver` or its concrete subclass `RSETHRateReceiver`: [2](#0-1) 

The only write path is `lzReceive`, which requires a valid LayerZero message from the configured provider: [3](#0-2) 

`getRate()` returns `rate` directly with no zero-check: [4](#0-3) 

Every L2 pool's `viewSwapRsETHAmountAndFee` calls `getRate()` and immediately divides by the result without guarding against zero. For example, in `RSETHPoolV3`: [5](#0-4) 

The same unguarded division appears in `RSETHPool`, `RSETHPoolNoWrapper`, `RSETHPoolV2`, `RSETHPoolV3ExternalBridge`, and `RSETHPoolV3WithNativeChainBridge`: [6](#0-5) [7](#0-6) 

The inconsistency is confirmed by the fact that `viewSwapAssetToPremintedRsETH` in the same contracts **does** guard against zero: [8](#0-7) 

Additionally, `lzReceive` accepts any decoded `uint256` value including `0` without validation: [9](#0-8) 

This means the zero-rate condition can persist beyond the deployment window if a LayerZero message carrying `rate = 0` is delivered (e.g., due to a bug in the provider encoding).

---

### Impact Explanation

**Medium — Temporary freezing of funds.**

While `rate == 0`, every call to `deposit()` (ETH or token variant) reverts with a division-by-zero panic. No user can swap ETH or LSTs for `wrsETH`/`rsETH` through any affected pool. Funds already held in the pool are not directly stolen, but the deposit service is completely frozen for all unprivileged users until a valid non-zero rate is delivered via LayerZero.

---

### Likelihood Explanation

**Low–Medium.**

The zero-rate window is bounded by the time between pool deployment and the first successful LayerZero rate propagation. In practice this window is short under normal conditions, but it is non-zero and is reachable by any user who attempts a deposit during that period. The secondary trigger (a zero-valued rate message) requires the rate provider to malfunction, which is a lower-probability event but is not blocked by any on-chain guard.

---

### Recommendation

Add a zero-rate guard in `viewSwapRsETHAmountAndFee` (both overloads) consistent with the guard already present in `viewSwapAssetToPremintedRsETH`:

```solidity
uint256 rsETHToETHrate = getRate();
if (rsETHToETHrate == 0) revert UnsupportedOracle();
```

Additionally, add a non-zero validation in `lzReceive` before writing to `rate`:

```solidity
require(_rate > 0, "Rate cannot be zero");
rate = _rate;
```

---

### Proof of Concept

1. Deploy `RSETHRateReceiver` (or any `CrossChainRateReceiver` subclass). `rate` is `0`.
2. Configure an L2 pool (e.g., `RSETHPoolV3`) with this receiver as `rsETHOracle`.
3. Before any LayerZero message arrives, call `deposit{value: 1 ether}("ref")`.
4. Execution reaches `viewSwapRsETHAmountAndFee(1 ether)`:
   - `rsETHToETHrate = getRate()` → returns `0`
   - `rsETHAmount = amountAfterFee * 1e18 / 0` → **Panic: division by zero**
5. Transaction reverts. All depositors are blocked until the first valid rate message is received. [1](#0-0) [10](#0-9)

### Citations

**File:** contracts/cross-chain/CrossChainRateReceiver.sol (L13-13)
```text
    uint256 public rate;
```

**File:** contracts/cross-chain/CrossChainRateReceiver.sol (L82-100)
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
```

**File:** contracts/cross-chain/CrossChainRateReceiver.sol (L103-105)
```text
    function getRate() external view returns (uint256) {
        return rate;
    }
```

**File:** contracts/cross-chain/RSETHRateReceiver.sol (L10-15)
```text
    constructor(uint16 _srcChainId, address _rateProvider, address _layerZeroEndpoint) {
        rateInfo = RateInfo({ tokenSymbol: "rsETH", baseTokenSymbol: "ETH" });
        srcChainId = _srcChainId;
        rateProvider = _rateProvider;
        layerZeroEndpoint = _layerZeroEndpoint;
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

**File:** contracts/pools/RSETHPoolV3ExternalBridge.sol (L523-524)
```text
        uint256 rsETHToETHrate = getRate();
        if (rsETHToETHrate == 0) revert UnsupportedOracle();
```

**File:** contracts/pools/RSETHPoolV3WithNativeChainBridge.sol (L335-344)
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
