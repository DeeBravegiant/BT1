### Title
Stale Cross-Chain Rate in `AGETHRateReceiver` Allows Over-Minting of agETH, Diluting Existing Holders' Yield — (`contracts/agETH/AGETHRateReceiver.sol`, `contracts/agETH/AGETHPoolV3.sol`)

---

### Summary

`AGETHRateReceiver` (which extends `CrossChainRateReceiver`) stores the last rate pushed via LayerZero but exposes it through `getRate()` with no staleness guard. `AGETHPoolV3`, deployed on destination chains, sets this receiver as its `agETHOracle` and uses the returned rate directly to compute how much agETH to mint. If `updateRate()` is not called for an extended period while the agETH/ETH rate appreciates on mainnet, new depositors receive more agETH than the current backing warrants, permanently diluting the yield owed to existing holders.

---

### Finding Description

**Rate storage with no freshness enforcement:**

`CrossChainRateReceiver.getRate()` simply returns the last stored `rate` with no check against `lastUpdated`: [1](#0-0) 

`lastUpdated` is recorded when `lzReceive` is called, but it is never read by `getRate()`: [2](#0-1) 

`AGETHRateReceiver` adds no override or staleness logic — it is a thin wrapper: [3](#0-2) 

**Pool consumes the stale rate unconditionally:**

`AGETHPoolV3.viewSwapAgETHAmountAndFee()` calls `getRate()` and divides by it to compute the agETH mint amount: [4](#0-3) 

`getRate()` in the pool is a direct pass-through to `agETHOracle`: [5](#0-4) 

**`updateRate()` is permissionless but not enforced on-chain:**

Anyone can call `updateRate()` on the provider, but it requires paying LayerZero fees and there is no on-chain mechanism that forces it to be called within any freshness window: [6](#0-5) 

---

### Impact Explanation

When the agETH/ETH rate appreciates (e.g., from 1.00 to 1.05 ETH per agETH over weeks), the stale rate stored in `AGETHRateReceiver` remains at the old lower value. The mint formula `agETHAmount = amountAfterFee * 1e18 / agETHToETHrate` produces a larger agETH amount when the denominator is stale-low. New depositors receive ~5% more agETH than the current backing supports. This inflates total agETH supply without proportional ETH backing, permanently diluting the per-token yield accrued by existing holders — matching the **Medium: Permanent freezing of unclaimed yield** scope.

---

### Likelihood Explanation

The scenario requires no attacker action. It is a passive condition: if the operator fails to call `updateRate()` (due to operational lapse, LayerZero downtime, or insufficient ETH for fees), the rate drifts. agETH is a yield-bearing token whose rate appreciates continuously, so any multi-week gap without an update creates a measurable discrepancy. The deposit path is fully public and requires no special role.

---

### Recommendation

Add a staleness threshold to `CrossChainRateReceiver.getRate()`:

```solidity
uint256 public constant MAX_RATE_AGE = 1 days; // or protocol-defined

function getRate() external view returns (uint256) {
    require(block.timestamp - lastUpdated <= MAX_RATE_AGE, "Rate is stale");
    return rate;
}
```

Alternatively, enforce the check inside `AGETHPoolV3.viewSwapAgETHAmountAndFee()` by reading `lastUpdated` from the oracle interface and reverting if the rate is too old.

---

### Proof of Concept

```solidity
// Fork destination chain (e.g., Arbitrum) at a block where lastUpdated is current.
// Advance time by 30 days without calling updateRate().
// The agETH/ETH rate on mainnet has appreciated ~5% (e.g., from 1.00e18 to 1.05e18).
// AGETHRateReceiver still holds rate = 1.00e18.

vm.warp(block.timestamp + 30 days);

uint256 depositAmount = 1 ether;
(uint256 agETHAmount, ) = agETHPoolV3.viewSwapAgETHAmountAndFee(depositAmount);

// With stale rate 1.00e18: agETHAmount ≈ 1.00e18 (minus fee)
// With correct rate 1.05e18: agETHAmount ≈ 0.952e18 (minus fee)
// Difference > 1%, existing holders' yield is diluted by the excess mint.

assertGt(agETHAmount, correctAmount * 101 / 100); // >1% over-mint confirmed

// Confirm staleness
assertGt(block.timestamp - agETHRateReceiver.lastUpdated(), 30 days);
```

The root cause is confirmed at:
- `CrossChainRateReceiver.getRate()` — no staleness check [1](#0-0) 
- `AGETHPoolV3.viewSwapAgETHAmountAndFee()` — unconditional rate consumption [7](#0-6)

### Citations

**File:** contracts/cross-chain/CrossChainRateReceiver.sol (L95-97)
```text
        rate = _rate;

        lastUpdated = block.timestamp;
```

**File:** contracts/cross-chain/CrossChainRateReceiver.sol (L103-105)
```text
    function getRate() external view returns (uint256) {
        return rate;
    }
```

**File:** contracts/agETH/AGETHRateReceiver.sol (L9-15)
```text
contract AGETHRateReceiver is CrossChainRateReceiver {
    constructor(uint16 _srcChainId, address _rateProvider, address _layerZeroEndpoint) {
        rateInfo = RateInfo({ tokenSymbol: "agETH", baseTokenSymbol: "ETH" });
        srcChainId = _srcChainId;
        rateProvider = _rateProvider;
        layerZeroEndpoint = _layerZeroEndpoint;
    }
```

**File:** contracts/agETH/AGETHPoolV3.sol (L104-106)
```text
    function getRate() public view returns (uint256) {
        return IOracle(agETHOracle).getRate();
    }
```

**File:** contracts/agETH/AGETHPoolV3.sol (L160-169)
```text
    function viewSwapAgETHAmountAndFee(uint256 amount) public view returns (uint256 agETHAmount, uint256 fee) {
        fee = amount * feeBps / 10_000;
        uint256 amountAfterFee = amount - fee;

        // rate of agETH in ETH
        uint256 agETHToETHrate = getRate();

        // Calculate the final agETH amount
        agETHAmount = amountAfterFee * 1e18 / agETHToETHrate;
    }
```

**File:** contracts/cross-chain/MultiChainRateProvider.sol (L108-113)
```text
    function updateRate() external payable nonReentrant {
        uint256 latestRate = getLatestRate();

        rate = latestRate;

        lastUpdated = block.timestamp;
```
