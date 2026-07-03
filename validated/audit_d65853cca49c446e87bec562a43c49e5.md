### Title
Uninitialized `rate` in `CrossChainRateReceiver` causes division-by-zero revert in all pool `deposit()` paths until first `lzReceive` succeeds — (`contracts/cross-chain/CrossChainRateReceiver.sol`)

---

### Summary

`CrossChainRateReceiver.rate` defaults to `0` at deployment. `getRate()` returns it unconditionally. Every pool variant divides by `rsETHToETHrate` without a zero-guard. Until the first successful `lzReceive` call, all pool swaps revert with a Solidity 0.8 division-by-zero panic.

---

### Finding Description

`CrossChainRateReceiver` stores the cross-chain rsETH/ETH rate in a `uint256 public rate` field, which Solidity zero-initializes. [1](#0-0) 

The only write path is `lzReceive`, which requires the LayerZero endpoint to call the contract with the correct `srcChainId` and `rateProvider`: [2](#0-1) 

`getRate()` returns `rate` with no zero-check or staleness check: [3](#0-2) 

Every pool contract (`RSETHPool`, `RSETHPoolV2`, `RSETHPoolV3`, `RSETHPoolNoWrapper`, `RSETHPoolV2NBA`, `RSETHPoolV2ExternalBridge`, `RSETHPoolV3ExternalBridge`, `RSETHPoolV3WithNativeChainBridge`) delegates its own `getRate()` directly to the oracle: [4](#0-3) 

Both swap paths then use `rsETHToETHrate` as a bare divisor:

- ETH path: `rsETHAmount = amountAfterFee * 1e18 / rsETHToETHrate` [5](#0-4) 

- Token path: `rsETHAmount = amountAfterFee * tokenToETHRate / rsETHToETHrate` [6](#0-5) 

In Solidity 0.8+, dividing by zero triggers a `Panic(0x12)` revert. There is no `require(rate != 0)` anywhere in `CrossChainRateReceiver`, `RSETHRateReceiver`, or any pool contract. [7](#0-6) 

---

### Impact Explanation

Any call to `deposit()` (ETH or token variant) on any pool that uses `RSETHRateReceiver` as its oracle will revert with a division-by-zero panic for as long as `rate == 0`. The pool cannot deliver its promised swap service. No funds are lost because the revert rolls back the entire transaction, but the contract is completely non-functional during this window.

Scope match: **Low — Contract fails to deliver promised returns, but doesn't lose value.**

---

### Likelihood Explanation

The window is bounded by the time between deployment and the first successful `lzReceive` delivery. This window is non-zero in every deployment. It is extended by:

- LayerZero message queue delays or dropped messages on the destination chain.
- Misconfigured `layerZeroEndpoint`, `srcChainId`, or `rateProvider` at deploy time (all three are set in the constructor with no on-chain validation that a live message can actually arrive).

The contract has been deployed across 15+ chains (Arbitrum, Optimism, Base, Scroll, Linea, Blast, zkSync, Unichain, Mantle, etc.), each with its own bootstrapping window. [8](#0-7) 

---

### Recommendation

1. Add a zero-rate guard in `CrossChainRateReceiver.getRate()`:
   ```solidity
   function getRate() external view returns (uint256) {
       require(rate != 0, "Rate not initialized");
       return rate;
   }
   ```
2. Optionally add a staleness check using `lastUpdated`:
   ```solidity
   require(block.timestamp - lastUpdated <= MAX_STALENESS, "Rate stale");
   ```
3. Consider allowing the owner to seed an initial rate at construction time so the pool is functional from block 0.

---

### Proof of Concept

```solidity
// Local unit test — no mainnet required
function test_zeroRateCausesRevert() public {
    // Deploy receiver with a mock endpoint that never delivers
    RSETHRateReceiver receiver = new RSETHRateReceiver(
        1,                  // srcChainId
        address(0xdead),    // rateProvider (never sends)
        address(mockLzEndpoint)
    );

    // Confirm rate is zero
    assertEq(receiver.getRate(), 0);

    // Wire pool to this receiver
    RSETHPoolV3 pool = deployPool(address(receiver));

    // Any deposit reverts with Panic(0x12) — division by zero
    vm.expectRevert(); // Panic: division by zero
    pool.deposit{value: 1 ether}("ref");
}
```

The revert is deterministic and reproducible on unmodified production code with no lzReceive call ever having succeeded. [9](#0-8)

### Citations

**File:** contracts/cross-chain/CrossChainRateReceiver.sol (L13-13)
```text
    uint256 public rate;
```

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

**File:** contracts/cross-chain/CrossChainRateReceiver.sol (L103-105)
```text
    function getRate() external view returns (uint256) {
        return rate;
    }
```

**File:** contracts/pools/RSETHPool.sol (L254-256)
```text
    function getRate() public view returns (uint256) {
        return IOracle(rsETHOracle).getRate();
    }
```

**File:** contracts/pools/RSETHPool.sol (L316-319)
```text
        uint256 rsETHToETHrate = getRate();

        // Calculate the final rsETH amount
        rsETHAmount = amountAfterFee * 1e18 / rsETHToETHrate;
```

**File:** contracts/pools/RSETHPool.sol (L340-346)
```text
        uint256 rsETHToETHrate = getRate();

        // rate of token in ETH
        uint256 tokenToETHRate = IOracle(supportedTokenOracle[token]).getRate();

        // Calculate the final rsETH amount
        rsETHAmount = amountAfterFee * tokenToETHRate / rsETHToETHrate;
```

**File:** contracts/cross-chain/RSETHRateReceiver.sol (L9-15)
```text
contract RSETHRateReceiver is CrossChainRateReceiver {
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
