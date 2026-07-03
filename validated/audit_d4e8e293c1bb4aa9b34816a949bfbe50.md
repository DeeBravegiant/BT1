### Title
Missing Zero-Rate Guard in `CrossChainRateReceiver.lzReceive` Causes Division-by-Zero in `RSETHPoolV3.viewSwapRsETHAmountAndFee`, Temporarily Freezing Deposits — (`contracts/cross-chain/CrossChainRateReceiver.sol`)

---

### Summary

`CrossChainRateReceiver.lzReceive` unconditionally stores any decoded `uint256` rate, including zero, with no validation. `LRTOracle.rsETHPrice` is a plain storage variable that defaults to `0` before `updateRSETHPrice()` is ever called. Because `CrossChainRateProvider.updateRate()` is permissionless, anyone can call it during the initialization window to broadcast a zero rate cross-chain. Once stored, `RSETHPoolV3.getRate()` returns `0`, and every subsequent call to `viewSwapRsETHAmountAndFee` panics with a division-by-zero, blocking all deposits until the rate is corrected.

---

### Finding Description

**Root cause 1 — no zero-rate guard in `lzReceive`:**

`CrossChainRateReceiver.lzReceive` decodes the payload and writes it directly to storage:

```solidity
uint256 _rate = abi.decode(_payload, (uint256));
rate = _rate;   // no require(_rate > 0)
``` [1](#0-0) 

**Root cause 2 — `LRTOracle.rsETHPrice` starts at zero:**

`rsETHPrice` is a plain `uint256` state variable; its initial value is `0` until `updateRSETHPrice()` / `_updateRsETHPrice()` is first called. [2](#0-1) 

`_updateRsETHPrice()` only sets a non-zero value after it is invoked: [3](#0-2) 

**Root cause 3 — permissionless `updateRate()`:**

Both `RSETHRateProvider` and `RSETHMultiChainRateProvider` inherit `updateRate()` with no access control, so any caller can push the current oracle value cross-chain at any time: [4](#0-3) 

`getLatestRate()` reads `rsETHPrice()` directly with no zero-check: [5](#0-4) 

**Root cause 4 — unchecked division in `viewSwapRsETHAmountAndFee`:**

`RSETHPoolV3.viewSwapRsETHAmountAndFee` divides by the rate without guarding against zero:

```solidity
uint256 rsETHToETHrate = getRate();
rsETHAmount = amountAfterFee * 1e18 / rsETHToETHrate;  // panics if 0
``` [6](#0-5) 

Both `deposit(string)` and `deposit(address,uint256,string)` call this function unconditionally: [7](#0-6) [8](#0-7) 

---

### Impact Explanation

**Correct impact: Medium — Temporary Freezing of Funds.**

The claimed "permanent" impact is overstated. The freeze lasts only until a valid (non-zero) rate is re-broadcast via `updateRate()` after the oracle is properly initialized. Once a non-zero rate is delivered and stored in `CrossChainRateReceiver.rate`, all deposit paths resume normally. The freeze is therefore **temporary**, not permanent.

During the freeze window, all calls to `RSETHPoolV3.deposit()` revert with a Solidity division-by-zero panic, preventing any new deposits on the L2 pool.

---

### Likelihood Explanation

**Low.** The precondition requires `LRTOracle.rsETHPrice == 0`, which is only true before `updateRSETHPrice()` is first called. In a correctly sequenced deployment this window is brief. However, because `updateRate()` is permissionless, a griefing actor or a race condition during deployment can exploit this window. The same issue would recur if the oracle is ever redeployed or reset without immediately calling `updateRSETHPrice()`.

---

### Recommendation

1. **Guard against zero in `lzReceive`:** Add `require(_rate > 0, "Rate must be non-zero");` before `rate = _rate` in `CrossChainRateReceiver.lzReceive`. [1](#0-0) 

2. **Guard against zero in `viewSwapRsETHAmountAndFee`:** Add `require(rsETHToETHrate > 0, "Invalid rate");` before the division. [6](#0-5) 

3. **Guard against zero in `updateRate()`:** Add `require(latestRate > 0, "Rate must be non-zero");` in `CrossChainRateProvider.updateRate()` and `MultiChainRateProvider.updateRate()` before encoding the payload. [9](#0-8) 

---

### Proof of Concept

```solidity
// Local unit test (no mainnet required)
function test_zerRateFreezesDeposits() public {
    // 1. Deploy LRTOracle but do NOT call updateRSETHPrice()
    //    => rsETHPrice == 0

    // 2. Anyone calls updateRate() on RSETHRateProvider
    rateProvider.updateRate{value: fee}();
    //    => payload = abi.encode(0) sent via LayerZero

    // 3. Simulate LayerZero delivery to RSETHRateReceiver
    vm.prank(layerZeroEndpoint);
    rateReceiver.lzReceive(srcChainId, srcAddressBytes, 0, abi.encode(uint256(0)));

    // 4. Assert rate is now 0
    assertEq(rateReceiver.getRate(), 0);

    // 5. RSETHPoolV3.deposit() reverts with division-by-zero
    vm.deal(user, 1 ether);
    vm.prank(user);
    vm.expectRevert(); // Panic: division by zero
    pool.deposit{value: 1 ether}("ref");
}
```

### Citations

**File:** contracts/cross-chain/CrossChainRateReceiver.sol (L93-95)
```text
        uint256 _rate = abi.decode(_payload, (uint256));

        rate = _rate;
```

**File:** contracts/LRTOracle.sol (L28-28)
```text
    uint256 public override rsETHPrice;
```

**File:** contracts/LRTOracle.sol (L218-221)
```text
        if (rsethSupply == 0) {
            rsETHPrice = 1 ether;
            highestRsethPrice = 1 ether;
            return;
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

**File:** contracts/cross-chain/RSETHRateProvider.sol (L27-28)
```text
    function getLatestRate() public view override returns (uint256) {
        return ILRTOracle(rsETHPriceOracle).rsETHPrice();
```

**File:** contracts/pools/RSETHPoolV3.sol (L258-258)
```text
        (uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount);
```

**File:** contracts/pools/RSETHPoolV3.sol (L286-286)
```text
        (uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount, token);
```

**File:** contracts/pools/RSETHPoolV3.sol (L304-307)
```text
        uint256 rsETHToETHrate = getRate();

        // Calculate the final rsETH amount
        rsETHAmount = amountAfterFee * 1e18 / rsETHToETHrate;
```
