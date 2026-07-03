### Title
Stale Cross-Chain Rate Enables Mispriced rsETH Minting via Block Stuffing — (`contracts/cross-chain/MultiChainRateProvider.sol`)

---

### Summary

`MultiChainRateProvider.updateRate()` is a permissionless, payable function with no access control. An attacker who stuffs L1 blocks can prevent it from being included, leaving `CrossChainRateReceiver.rate` stale. Because neither `CrossChainRateReceiver.getRate()` nor `RSETHPoolV3.viewSwapRsETHAmountAndFee()` enforce any rate-freshness check, destination-chain depositors receive more rsETH than the current L1 backing warrants.

---

### Finding Description

**Rate propagation path:**

`RSETHMultiChainRateProvider.getLatestRate()` reads `ILRTOracle(rsETHPriceOracle).rsETHPrice()` on L1. [1](#0-0) 

`MultiChainRateProvider.updateRate()` is `external payable nonReentrant` — no role gate, no `onlyOwner`. [2](#0-1) 

On the destination chain, `CrossChainRateReceiver.lzReceive()` stores the received value in `rate` and `lastUpdated`, but `getRate()` returns `rate` unconditionally — no staleness window is enforced. [3](#0-2) 

`RSETHPoolV3.viewSwapRsETHAmountAndFee()` divides by whatever `getRate()` returns with no freshness check:

```solidity
uint256 rsETHToETHrate = getRate();
rsETHAmount = amountAfterFee * 1e18 / rsETHToETHrate;
``` [4](#0-3) 

**Attack sequence:**

1. Attacker monitors L1 `LRTOracle.rsETHPrice()` rising (it rises continuously as staking rewards accrue).
2. Attacker floods L1 blocks with high-gas-price dummy transactions, consuming all block space and excluding `updateRate()` calls.
3. `CrossChainRateReceiver.rate` remains at the old, lower value.
4. On the destination chain, `RSETHPoolV3.deposit()` computes `rsETHAmount = amountAfterFee * 1e18 / staleRate`. Because `staleRate < currentL1Rate`, the quotient is larger than it should be.
5. `wrsETH.mint(msg.sender, rsETHAmount)` issues excess rsETH shares. [5](#0-4) 

The `dailyMintLimit` caps total daily issuance but does not prevent the per-deposit mispricing within that cap. [6](#0-5) 

---

### Impact Explanation

Every rsETH minted on a destination chain while the rate is stale is under-collateralised relative to the current L1 rsETH/ETH rate. The invariant that each destination-chain rsETH share is fully backed by the current L1 rate is violated. The `dailyMintLimit` bounds the total damage per day but does not eliminate it.

**Scoped impact: Low — Block stuffing / contract fails to deliver promised returns.**

---

### Likelihood Explanation

Block stuffing on Ethereum mainnet is expensive but not impossible; it has been executed in production (e.g., NFT-mint races). The attack is profitable when the rate delta times the daily mint cap exceeds the cost of filling blocks for the stuffing window. Because rsETH accrues staking rewards continuously, even a short stuffing window (minutes to hours) creates a measurable rate gap. The permissionless nature of `updateRate()` means there is no privileged caller whose key must be compromised — the attacker only needs to outbid the gas market.

---

### Recommendation

1. **Add a staleness guard in `CrossChainRateReceiver.getRate()`** — revert if `block.timestamp - lastUpdated > MAX_STALENESS` (e.g., 24 hours).
2. **Add the same guard in `RSETHPoolV3.viewSwapRsETHAmountAndFee()`** — revert or pause deposits when the oracle rate is stale.
3. Consider making `updateRate()` callable only by a trusted keeper role, reducing the surface for griefing while still allowing permissioned updates.

---

### Proof of Concept

```solidity
// Fork test (Foundry) — no mainnet calls, fork only
// 1. Fork L1 at a block where rsETHPrice = P0.
// 2. Warp time forward (simulating block stuffing window) without calling updateRate().
//    CrossChainRateReceiver.rate remains P0.
// 3. On the destination chain fork, rsETHPrice on LRTOracle is now P1 > P0
//    (staking rewards accrued).
// 4. Call RSETHPoolV3.deposit{value: 1 ether}("").
// 5. Compute expected = 1 ether * 1e18 / P1 (correct amount).
// 6. Assert wrsETH.balanceOf(depositor) > expected.
//    → Depositor received more rsETH than current L1 backing warrants.
```

The assertion holds because `viewSwapRsETHAmountAndFee` divides by the stale `P0 < P1`, yielding a larger `rsETHAmount` than the current rate justifies. [7](#0-6) [8](#0-7)

### Citations

**File:** contracts/cross-chain/RSETHMultiChainRateProvider.sol (L26-28)
```text
    function getLatestRate() public view override returns (uint256) {
        return ILRTOracle(rsETHPriceOracle).rsETHPrice();
    }
```

**File:** contracts/cross-chain/MultiChainRateProvider.sol (L108-137)
```text
    function updateRate() external payable nonReentrant {
        uint256 latestRate = getLatestRate();

        rate = latestRate;

        lastUpdated = block.timestamp;

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

        emit RateUpdated(rate);
    }
```

**File:** contracts/cross-chain/CrossChainRateReceiver.sol (L93-97)
```text
        uint256 _rate = abi.decode(_payload, (uint256));

        rate = _rate;

        lastUpdated = block.timestamp;
```

**File:** contracts/cross-chain/CrossChainRateReceiver.sol (L103-105)
```text
    function getRate() external view returns (uint256) {
        return rate;
    }
```

**File:** contracts/pools/RSETHPoolV3.sol (L119-123)
```text
        if (dailyMintAmount + rsETHAmount > dailyMintLimit) {
            revert DailyMintLimitExceeded();
        }

        dailyMintAmount += rsETHAmount;
```

**File:** contracts/pools/RSETHPoolV3.sol (L258-263)
```text
        (uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount);

        feeEarnedInETH += fee;

        wrsETH.mint(msg.sender, rsETHAmount);

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
