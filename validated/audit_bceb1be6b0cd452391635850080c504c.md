### Title
Stale `rsETHPrice` Broadcast via Permissionless `updateRate()` — (`contracts/cross-chain/RSETHMultiChainRateProvider.sol`)

---

### Summary

`RSETHMultiChainRateProvider.updateRate()` is callable by any address with no access control and no oracle-freshness check. It reads `LRTOracle.rsETHPrice`, a stored state variable that is only updated when `updateRSETHPrice()` is explicitly called. If that function has not been called recently, `updateRate()` silently broadcasts a stale rate to every LayerZero destination chain.

---

### Finding Description

**Call chain:**

```
anyone → MultiChainRateProvider.updateRate()
           └─ RSETHMultiChainRateProvider.getLatestRate()
                └─ ILRTOracle(rsETHPriceOracle).rsETHPrice()   // stored slot, not recomputed
```

`updateRate()` carries no role modifier: [1](#0-0) 

`getLatestRate()` in the concrete provider simply reads the stored slot: [2](#0-1) 

`LRTOracle.rsETHPrice` is only written inside `_updateRsETHPrice()`, which is only triggered by explicit calls to `updateRSETHPrice()` or `updateRSETHPriceAsManager()`: [3](#0-2) [4](#0-3) 

Neither `MultiChainRateProvider` nor `RSETHMultiChainRateProvider` checks any timestamp or freshness window before accepting and forwarding the stored value. The provider's own `lastUpdated` field records when `updateRate()` was last called, not when the oracle price was last computed: [5](#0-4) [6](#0-5) 

---

### Impact Explanation

Any caller can invoke `updateRate()` at any time. If `updateRSETHPrice()` has not been called for an extended period (e.g., 48 hours), the stale `rsETHPrice` is encoded into the LayerZero payload and sent to every registered receiver: [7](#0-6) 

Destination-chain pools that rely on the received rate will price rsETH deposits/withdrawals against a value that no longer reflects the true ETH backing per rsETH share. This matches the scoped impact: **the contract fails to deliver the promised (current) exchange rate, though no funds are directly stolen**.

---

### Likelihood Explanation

- `updateRate()` is permissionless — no keeper exclusivity.
- `updateRSETHPrice()` is also public, so a well-behaved caller could refresh the oracle first, but nothing enforces this ordering.
- A griefing actor (or simply an inattentive keeper schedule) can call `updateRate()` while the oracle is stale, locking in an outdated rate on all destination chains until the next `updateRate()` call with a fresh oracle.
- The longer the oracle goes without a refresh, the larger the deviation between the broadcast rate and the true rate (rsETH accrues yield continuously).

---

### Recommendation

Inside `RSETHMultiChainRateProvider.getLatestRate()` (or at the top of `updateRate()`), call `ILRTOracle(rsETHPriceOracle).updateRSETHPrice()` before reading `rsETHPrice`, so the rate is always freshly computed at broadcast time. Alternatively, record the oracle's last-update timestamp and revert in `updateRate()` if the stored price is older than an acceptable freshness window (e.g., 1 hour).

---

### Proof of Concept

```solidity
// Fork mainnet, pin block B (rsETHPrice = P0)
// Advance time by 48 hours — do NOT call updateRSETHPrice()
vm.warp(block.timestamp + 48 hours);

// Compute what the true price would be now
uint256 trueRate = lrtOracle._getTotalEthInProtocol_exposed() * 1e18
                   / rsETH.totalSupply();

// Anyone calls updateRate() — no role required
multiChainProvider.updateRate{value: fee}();

// The emitted RateUpdated value equals the pre-warp stored slot
assertEq(multiChainProvider.rate(), P0);          // stale
assertTrue(trueRate > P0);                         // true rate is higher
// All LayerZero receivers now hold P0, not trueRate
```

This is locally reproducible on a mainnet fork with no admin keys or external compromise required.

### Citations

**File:** contracts/cross-chain/MultiChainRateProvider.sol (L18-18)
```text
    uint256 public lastUpdated;
```

**File:** contracts/cross-chain/MultiChainRateProvider.sol (L108-108)
```text
    function updateRate() external payable nonReentrant {
```

**File:** contracts/cross-chain/MultiChainRateProvider.sol (L111-113)
```text
        rate = latestRate;

        lastUpdated = block.timestamp;
```

**File:** contracts/cross-chain/MultiChainRateProvider.sol (L115-134)
```text
        bytes memory _payload = abi.encode(latestRate);

        uint256 rateReceiversLength = rateReceivers.length;

        for (uint256 i; i < rateReceiversLength;) {
            uint16 dstChainId = uint16(rateReceivers[i]._chainId);

            bytes memory remoteAndLocalAddresses = abi.encodePacked(rateReceivers[i]._contract, address(this));

            (uint256 estimatedFee,) = ILayerZeroEndpoint(layerZeroEndpoint)
                .estimateFees(dstChainId, address(this), _payload, false, bytes(""));

            ILayerZeroEndpoint(layerZeroEndpoint).send{ value: estimatedFee }(
                dstChainId, remoteAndLocalAddresses, _payload, payable(msg.sender), address(0x0), bytes("")
            );

            unchecked {
                ++i;
            }
        }
```

**File:** contracts/cross-chain/RSETHMultiChainRateProvider.sol (L26-28)
```text
    function getLatestRate() public view override returns (uint256) {
        return ILRTOracle(rsETHPriceOracle).rsETHPrice();
    }
```

**File:** contracts/LRTOracle.sol (L87-89)
```text
    function updateRSETHPrice() public whenNotPaused {
        _updateRsETHPrice();
    }
```

**File:** contracts/LRTOracle.sol (L313-313)
```text
        rsETHPrice = newRsETHPrice;
```
