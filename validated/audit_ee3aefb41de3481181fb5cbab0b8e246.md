### Title
Stale agETH/ETH Rate Due to Block Stuffing Enables Excess agETH Minting — (`contracts/cross-chain/CrossChainRateReceiver.sol`)

---

### Summary

`CrossChainRateReceiver.lzReceive` is the sole mechanism for updating the on-chain agETH/ETH rate. It has no staleness guard, and `getRate()` returns whatever value was last written. An attacker who block-stuffs the destination chain can prevent the LayerZero relayer from landing the rate-update transaction, leaving `AGETHPoolV3` consuming a stale (artificially low) rate and minting excess agETH to depositors.

---

### Finding Description

`lzReceive` writes `rate` and `lastUpdated` but neither `getRate()` nor `AGETHPoolV3.viewSwapAgETHAmountAndFee` ever checks `lastUpdated` against a maximum-age threshold. [1](#0-0) 

`getRate()` is a plain storage read with no freshness assertion: [2](#0-1) 

`AGETHPoolV3.getRate()` delegates directly to the oracle with no additional check: [3](#0-2) 

Both deposit paths (`ETH` and `token`) call `viewSwapAgETHAmountAndFee`, which divides by the stale rate: [4](#0-3) [5](#0-4) 

If `agETHToETHrate` is stale-low (e.g., `R0 = 1.00e18`) while the true rate has appreciated (e.g., `R1 = 1.05e18`), the division `amountAfterFee * 1e18 / R0` yields ~5 % more agETH than `amountAfterFee * 1e18 / R1`.

---

### Impact Explanation

Every depositor during the stale window receives more agETH than the deposited collateral is worth in agETH terms. The minted agETH is unbacked by the corresponding assets, violating the pool's backing invariant. The magnitude scales with (a) how much agETH has appreciated and (b) the total deposit volume during the stale window.

Scoped impact: **Low — Contract fails to deliver promised returns / block stuffing.**

---

### Likelihood Explanation

Block stuffing is expensive but feasible on low-fee L2 chains where `AGETHPoolV3` is deployed. The attacker only needs to sustain the stuffing long enough to prevent a single LZ relay transaction from landing. Because `lzReceive` is permissioned to `layerZeroEndpoint` only, there is no alternative path to push a fresh rate during the attack. [6](#0-5) 

---

### Recommendation

Add a maximum-age check inside `getRate()` (or inside `AGETHPoolV3.getRate()`):

```solidity
uint256 public constant MAX_RATE_AGE = 1 days;

function getRate() external view returns (uint256) {
    require(block.timestamp - lastUpdated <= MAX_RATE_AGE, "Rate too stale");
    return rate;
}
```

This causes deposits to revert rather than proceed with a stale rate, eliminating the excess-minting window.

---

### Proof of Concept

```solidity
// Fork test (destination chain fork)
function test_blockStuffingStaleRate() external {
    // 1. Deploy AGETHRateReceiver with initial rate R0 = 1.00e18
    AGETHRateReceiver receiver = new AGETHRateReceiver(...);
    // lzReceive sets rate = 1.00e18
    vm.prank(lzEndpoint);
    receiver.lzReceive(srcChainId, abi.encodePacked(rateProvider), 0,
                       abi.encode(uint256(1.00e18)));

    // 2. Simulate block stuffing: skip time without calling lzReceive again
    //    (agETH appreciates to R1 = 1.05e18 on L1, but no update lands)
    skip(2 days);

    // 3. Deploy AGETHPoolV3 pointing at the stale receiver
    AGETHPoolV3 pool = ...; // agETHOracle = address(receiver)

    // 4. Deposit 1 ETH
    uint256 agETHStale = pool.viewSwapAgETHAmountAndFee(1 ether).agETHAmount;
    // agETHStale = 1e18 * 1e18 / 1.00e18 = 1.00e18

    // 5. Simulate fresh rate update
    vm.prank(lzEndpoint);
    receiver.lzReceive(srcChainId, abi.encodePacked(rateProvider), 1,
                       abi.encode(uint256(1.05e18)));
    uint256 agETHFresh = pool.viewSwapAgETHAmountAndFee(1 ether).agETHAmount;
    // agETHFresh = 1e18 * 1e18 / 1.05e18 ≈ 0.952e18

    // 6. Assert stale > fresh (excess minting confirmed)
    assertGt(agETHStale, agETHFresh);
}
```

### Citations

**File:** contracts/cross-chain/CrossChainRateReceiver.sol (L82-83)
```text
    function lzReceive(uint16 _srcChainId, bytes memory _srcAddress, uint64, bytes calldata _payload) external {
        require(msg.sender == layerZeroEndpoint, "Sender should be lz endpoint");
```

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

**File:** contracts/agETH/AGETHPoolV3.sol (L104-106)
```text
    function getRate() public view returns (uint256) {
        return IOracle(agETHOracle).getRate();
    }
```

**File:** contracts/agETH/AGETHPoolV3.sol (L160-168)
```text
    function viewSwapAgETHAmountAndFee(uint256 amount) public view returns (uint256 agETHAmount, uint256 fee) {
        fee = amount * feeBps / 10_000;
        uint256 amountAfterFee = amount - fee;

        // rate of agETH in ETH
        uint256 agETHToETHrate = getRate();

        // Calculate the final agETH amount
        agETHAmount = amountAfterFee * 1e18 / agETHToETHrate;
```

**File:** contracts/agETH/AGETHPoolV3.sol (L183-194)
```text
    {
        fee = amount * feeBps / 10_000;
        uint256 amountAfterFee = amount - fee;

        // rate of agETH in ETH
        uint256 agETHToETHrate = getRate();

        // rate of token in ETH
        uint256 tokenToETHRate = IOracle(supportedTokenOracle[token]).getRate();

        // Calculate the final agETH amount
        agETHAmount = amountAfterFee * tokenToETHRate / agETHToETHrate;
```
